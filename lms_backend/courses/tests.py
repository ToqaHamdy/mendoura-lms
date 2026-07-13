from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import (
    Category, Certificate, Course, Enrollment, InstructorWallet, Lecture, Module,
    Payment, Payout, Review, Track, User, WalletTransaction,
)
from .money import calculate_split


class SplitCalculationTests(TestCase):
    """The money math is the highest-risk part of this project."""

    def test_no_lost_cents(self):
        """The most important test in the whole project."""
        for cents in range(1, 100_00):  # $0.01 -> $100.00
            total = Decimal(cents) / Decimal('100')
            for pct in (Decimal('70.00'), Decimal('50.00')):
                inst, plat = calculate_split(total, pct)
                assert inst + plat == total  # nothing lost to rounding
                assert inst >= 0 and plat >= 0

    def test_awkward_rounding(self):
        # 19.99 x 0.70 = 13.993 -- must not raise, must not lose a cent
        self.assertEqual(
            calculate_split(Decimal('19.99'), Decimal('70.00')),
            (Decimal('13.99'), Decimal('6.00')),
        )

    def test_fifty_fifty_odd_cent(self):
        # 9.99 / 2 = 4.995 -- the half-cent goes to the platform
        self.assertEqual(
            calculate_split(Decimal('9.99'), Decimal('50.00')),
            (Decimal('4.99'), Decimal('5.00')),
        )


class PaymentModelTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='instructor1', password='pw', is_instructor=True)
        self.student = User.objects.create_user(
            username='student1', password='pw', is_student=True)
        self.track = Track.objects.create(name='Web Development')
        self.course = Course.objects.create(
            instructor=self.instructor,
            track=self.track,
            title='Django Basics',
            description='Learn Django',
            production_type=Course.ProductionType.FULL,
            price=Decimal('19.99'),
        )

    def test_payment_snapshots_split_at_creation(self):
        payment = Payment.objects.create(
            student=self.student, course=self.course, total_amount=Decimal('19.99'))
        self.assertEqual(payment.production_type_at_purchase, Course.ProductionType.FULL)
        self.assertEqual(payment.instructor_share_percentage, Decimal('70.00'))
        self.assertEqual(payment.instructor_amount, Decimal('13.99'))
        self.assertEqual(payment.platform_amount, Decimal('6.00'))
        self.assertEqual(payment.instructor_amount + payment.platform_amount, payment.total_amount)

    def test_payment_frozen_fields_are_immutable(self):
        payment = Payment.objects.create(
            student=self.student, course=self.course, total_amount=Decimal('19.99'))
        payment.total_amount = Decimal('999.00')
        with self.assertRaises(ValidationError):
            payment.save()

    def test_payment_snapshot_survives_course_production_type_change(self):
        payment = Payment.objects.create(
            student=self.student, course=self.course, total_amount=Decimal('19.99'))
        # A later course, or a hypothetical future production_type change, must
        # never retroactively alter a historical payment's snapshot.
        self.assertEqual(payment.instructor_share_percentage, Decimal('70.00'))
        payment.refresh_from_db()
        self.assertEqual(payment.instructor_share_percentage, Decimal('70.00'))

    def test_production_type_locked_after_first_successful_sale(self):
        Payment.objects.create(
            student=self.student, course=self.course, total_amount=Decimal('19.99'),
            status=Payment.Status.SUCCEEDED)
        self.course.production_type = Course.ProductionType.SCRIPT_ONLY
        with self.assertRaises(ValidationError):
            self.course.save()

    def test_production_type_changeable_before_any_sale(self):
        self.course.production_type = Course.ProductionType.SCRIPT_ONLY
        self.course.save()  # no successful payment yet -- must not raise
        self.course.refresh_from_db()
        self.assertEqual(self.course.production_type, Course.ProductionType.SCRIPT_ONLY)


class WalletTransactionLedgerTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='instructor2', password='pw', is_instructor=True)
        self.wallet = InstructorWallet.objects.create(instructor=self.instructor)

    def test_wallet_credit_updates_balances_and_ledger(self):
        credit = Decimal('13.99')
        self.wallet.available_balance += credit
        self.wallet.total_earnings += credit
        self.wallet.save()
        txn = WalletTransaction.objects.create(
            wallet=self.wallet, type=WalletTransaction.Type.SALE_CREDIT,
            amount=credit, balance_after=self.wallet.available_balance,
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, credit)
        self.assertEqual(txn.balance_after, self.wallet.available_balance)

    def test_ledger_rows_are_append_only(self):
        txn = WalletTransaction.objects.create(
            wallet=self.wallet, type=WalletTransaction.Type.SALE_CREDIT,
            amount=Decimal('10.00'), balance_after=Decimal('10.00'),
        )
        txn.amount = Decimal('999.00')
        with self.assertRaises(ValidationError):
            txn.save()
        with self.assertRaises(ValidationError):
            txn.delete()


class LectureAccessControlTests(TestCase):
    """An unenrolled student must not reach lecture content by guessing a URL."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='inst', password='pw', is_instructor=True)
        self.enrolled_student = User.objects.create_user(
            username='enrolled', password='pw', is_student=True)
        self.outside_student = User.objects.create_user(
            username='outsider', password='pw', is_student=True)
        track = Track.objects.create(name='Web Development')
        self.course = Course.objects.create(
            instructor=self.instructor, track=track, title='Django Basics',
            description='...', production_type=Course.ProductionType.FULL,
            price=Decimal('0.00'), is_free=True, status=Course.Status.PUBLISHED,
        )
        module = Module.objects.create(course=self.course, title='Module 1')
        self.preview_lecture = Lecture.objects.create(
            module=module, title='Intro', is_preview=True)
        self.locked_lecture = Lecture.objects.create(
            module=module, title='Deep Dive', is_preview=False)
        Enrollment.objects.create(student=self.enrolled_student, course=self.course)

    def _player_url(self, lecture):
        return reverse('course_player', args=[self.course.id, lecture.id])

    def test_anonymous_user_can_watch_preview_lecture(self):
        response = self.client.get(self._player_url(self.preview_lecture))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_redirected_to_login_for_locked_lecture(self):
        response = self.client.get(self._player_url(self.locked_lecture))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_unenrolled_student_cannot_reach_locked_lecture(self):
        self.client.force_login(self.outside_student)
        response = self.client.get(self._player_url(self.locked_lecture))
        self.assertEqual(response.status_code, 403)

    def test_unenrolled_student_can_still_watch_preview_lecture(self):
        self.client.force_login(self.outside_student)
        response = self.client.get(self._player_url(self.preview_lecture))
        self.assertEqual(response.status_code, 200)

    def test_enrolled_student_can_reach_locked_lecture(self):
        self.client.force_login(self.enrolled_student)
        response = self.client.get(self._player_url(self.locked_lecture))
        self.assertEqual(response.status_code, 200)


class EnrollmentAndReviewTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='inst2', password='pw', is_instructor=True)
        self.student = User.objects.create_user(
            username='stud2', password='pw', is_student=True)
        track = Track.objects.create(name='Data Science & AI')
        self.free_course = Course.objects.create(
            instructor=self.instructor, track=track, title='Intro to Pandas',
            description='...', production_type=Course.ProductionType.SCRIPT_ONLY,
            price=Decimal('0.00'), is_free=True, status=Course.Status.PUBLISHED,
        )

    def test_enroll_free_course_is_instant(self):
        self.client.force_login(self.student)
        self.client.post(reverse('enroll_course', args=[self.free_course.id]))
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.free_course).exists())

    def test_only_enrolled_students_can_review(self):
        self.client.force_login(self.student)
        self.client.post(reverse('add_review', args=[self.free_course.id]),
                          {'rating': 5, 'comment': 'Great!'})
        self.assertFalse(Review.objects.filter(student=self.student).exists())

        Enrollment.objects.create(student=self.student, course=self.free_course)
        self.client.post(reverse('add_review', args=[self.free_course.id]),
                          {'rating': 5, 'comment': 'Great!'})
        self.assertTrue(Review.objects.filter(student=self.student).exists())

    def test_completing_all_lectures_issues_certificate(self):
        module = Module.objects.create(course=self.free_course, title='Module 1')
        lecture = Lecture.objects.create(module=module, title='Only Lecture')
        enrollment = Enrollment.objects.create(student=self.student, course=self.free_course)

        self.client.force_login(self.student)
        self.client.post(reverse('mark_lecture_complete', args=[self.free_course.id, lecture.id]))

        self.assertTrue(Certificate.objects.filter(enrollment=enrollment).exists())


class InstructorIsolationTests(TestCase):
    """An instructor must not see another instructor's courses, students, or wallet."""

    def setUp(self):
        self.track = Track.objects.create(name='Cloud & DevOps')
        self.owner = User.objects.create_user(username='owner', password='pw', is_instructor=True)
        self.intruder = User.objects.create_user(username='intruder', password='pw', is_instructor=True)
        self.course = Course.objects.create(
            instructor=self.owner, track=self.track, title='Owner Course', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('0.00'), is_free=True,
        )
        self.module = Module.objects.create(course=self.course, title='M1')

    def test_cannot_manage_modules_of_anothers_course(self):
        self.client.force_login(self.intruder)
        response = self.client.get(reverse('manage_modules', args=[self.course.id]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_manage_lectures_of_anothers_course(self):
        self.client.force_login(self.intruder)
        response = self.client.get(
            reverse('manage_lectures', args=[self.course.id, self.module.id]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_view_students_of_anothers_course(self):
        self.client.force_login(self.intruder)
        response = self.client.get(reverse('course_students', args=[self.course.id]))
        self.assertEqual(response.status_code, 404)

    def test_wallet_view_is_scoped_to_the_logged_in_instructor(self):
        owner_wallet = InstructorWallet.objects.create(instructor=self.owner, available_balance=Decimal('42.00'))
        InstructorWallet.objects.create(instructor=self.intruder, available_balance=Decimal('0.00'))

        self.client.force_login(self.intruder)
        response = self.client.get(reverse('instructor_wallet'))
        self.assertNotContains(response, '42.00')


class PayoutRequestTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='payout_inst', password='pw', is_instructor=True)
        self.wallet = InstructorWallet.objects.create(
            instructor=self.instructor, available_balance=Decimal('20.00'))

    def test_cannot_request_more_than_available_balance(self):
        self.client.force_login(self.instructor)
        self.client.post(reverse('request_payout'), {'amount': '50.00', 'method': 'bank'})
        self.assertFalse(Payout.objects.filter(wallet=self.wallet).exists())

    def test_can_request_up_to_available_balance(self):
        self.client.force_login(self.instructor)
        self.client.post(reverse('request_payout'), {'amount': '20.00', 'method': 'bank'})
        self.assertTrue(Payout.objects.filter(wallet=self.wallet, amount=Decimal('20.00')).exists())

    def test_requesting_reserves_the_amount_so_it_cannot_be_double_spent(self):
        self.client.force_login(self.instructor)
        self.client.post(reverse('request_payout'), {'amount': '20.00', 'method': 'bank'})
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal('0.00'))
        self.assertEqual(self.wallet.pending_balance, Decimal('20.00'))

        # A second request against the now-empty available balance must fail.
        self.client.post(reverse('request_payout'), {'amount': '20.00', 'method': 'bank'})
        self.assertEqual(Payout.objects.filter(wallet=self.wallet).count(), 1)


class AdminGuardTests(TestCase):
    """Every admin view must be guarded by a real permission check in the view."""

    def setUp(self):
        self.student = User.objects.create_user(username='plain_student', password='pw', is_student=True)
        self.instructor = User.objects.create_user(
            username='plain_instructor', password='pw', is_instructor=True)
        self.admin = User.objects.create_superuser(username='real_admin', password='pw')

    def test_non_admin_cannot_reach_admin_dashboard(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('admin_dashboard'))
        self.assertNotEqual(response.status_code, 200)

    def test_instructor_cannot_reach_admin_pages(self):
        self.client.force_login(self.instructor)
        for name in ('course_approval_queue', 'admin_users', 'admin_payments',
                     'admin_payouts', 'admin_tracks', 'admin_categories'):
            response = self.client.get(reverse(name))
            self.assertNotEqual(response.status_code, 200, f'{name} was reachable by a non-admin')

    def test_admin_can_reach_admin_pages(self):
        self.client.force_login(self.admin)
        for name in ('admin_dashboard', 'course_approval_queue', 'admin_users', 'admin_payments',
                     'admin_payouts', 'admin_tracks', 'admin_categories'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, f'{name} was not reachable by an admin')

    def test_anonymous_user_cannot_reach_admin_dashboard(self):
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 302)


class CourseApprovalTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='approver', password='pw')
        self.instructor = User.objects.create_user(
            username='pending_inst', password='pw', is_instructor=True)
        track = Track.objects.create(name='UI/UX Design')
        self.course = Course.objects.create(
            instructor=self.instructor, track=track, title='Pending Course', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('0.00'), is_free=True,
            status=Course.Status.PENDING_REVIEW,
        )

    def test_approve_publishes_course(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('approve_course', args=[self.course.id]))
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, Course.Status.PUBLISHED)

    def test_reject_stores_reason(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('reject_course', args=[self.course.id]), {'reason': 'Low quality audio'})
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, Course.Status.REJECTED)
        self.assertEqual(self.course.rejection_reason, 'Low quality audio')

    def test_instructor_cannot_approve_own_course(self):
        self.client.force_login(self.instructor)
        response = self.client.post(reverse('approve_course', args=[self.course.id]))
        self.assertNotEqual(response.status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, Course.Status.PENDING_REVIEW)


class AdminPayoutLifecycleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='payout_admin', password='pw')
        self.instructor = User.objects.create_user(
            username='payout_recipient', password='pw', is_instructor=True)
        self.wallet = InstructorWallet.objects.create(
            instructor=self.instructor, available_balance=Decimal('0.00'),
            pending_balance=Decimal('30.00'))
        self.payout = Payout.objects.create(wallet=self.wallet, amount=Decimal('30.00'), method='bank')

    def test_approve_then_mark_paid_moves_pending_to_withdrawn(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('approve_payout', args=[self.payout.id]))
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.APPROVED)

        self.client.post(reverse('mark_payout_paid', args=[self.payout.id]))
        self.payout.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.PAID)
        self.assertEqual(self.wallet.pending_balance, Decimal('0.00'))
        self.assertEqual(self.wallet.total_withdrawn, Decimal('30.00'))
        self.assertTrue(WalletTransaction.objects.filter(
            wallet=self.wallet, type=WalletTransaction.Type.WITHDRAWAL, amount=Decimal('30.00')).exists())

    def test_reject_returns_funds_to_available_balance(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('reject_payout', args=[self.payout.id]))
        self.payout.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.REJECTED)
        self.assertEqual(self.wallet.pending_balance, Decimal('0.00'))
        self.assertEqual(self.wallet.available_balance, Decimal('30.00'))


class TrackCategoryCrudTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='crud_admin', password='pw')

    def test_admin_can_create_and_deactivate_track(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('admin_tracks'), {'name': 'Robotics', 'description': '', 'icon': '', 'order': 0})
        track = Track.objects.get(name='Robotics')
        self.assertTrue(track.is_active)

        self.client.post(reverse('toggle_track_active', args=[track.id]))
        track.refresh_from_db()
        self.assertFalse(track.is_active)

    def test_admin_can_create_and_delete_category(self):
        track = Track.objects.create(name='Data Science & AI')
        self.client.force_login(self.admin)
        self.client.post(reverse('admin_categories'), {'track': track.id, 'name': 'Machine Learning'})
        category = Category.objects.get(name='Machine Learning')

        self.client.post(reverse('delete_category', args=[category.id]))
        self.assertFalse(Category.objects.filter(id=category.id).exists())
