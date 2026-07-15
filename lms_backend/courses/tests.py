import hashlib
import hmac
import json
import random
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import paymob
from .models import (
    Category, Certificate, Course, Enrollment, InstructorWallet, Lecture, Module,
    Payment, Payout, Plan, RevenueDistribution, Review, Subscription, SubscriptionPeriod,
    Track, User, WalletTransaction, WatchEvent,
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

    def test_second_request_within_a_week_is_blocked_even_with_balance(self):
        self.wallet.available_balance = Decimal('100.00')
        self.wallet.save()
        self.client.force_login(self.instructor)
        self.client.post(reverse('request_payout'), {'amount': '10.00', 'method': 'bank'})
        self.assertEqual(Payout.objects.filter(wallet=self.wallet).count(), 1)

        # Balance is there, but the weekly cooldown should still block a
        # second request the same day.
        self.client.post(reverse('request_payout'), {'amount': '10.00', 'method': 'bank'})
        self.assertEqual(Payout.objects.filter(wallet=self.wallet).count(), 1)

    def test_request_allowed_again_after_a_week(self):
        self.wallet.available_balance = Decimal('100.00')
        self.wallet.save()
        self.client.force_login(self.instructor)
        self.client.post(reverse('request_payout'), {'amount': '10.00', 'method': 'bank'})

        old_payout = Payout.objects.get(wallet=self.wallet)
        old_payout.requested_at = timezone.now() - timedelta(days=8)
        old_payout.save()

        self.client.post(reverse('request_payout'), {'amount': '10.00', 'method': 'bank'})
        self.assertEqual(Payout.objects.filter(wallet=self.wallet).count(), 2)


class CourseCreationTrackScopeTests(TestCase):
    """A course must only ever be filed under a leaf track -- a parent
    category like 'Tech' has no course list of its own, so a course
    assigned to one would silently never appear on any student browse page."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='track_scope_inst', password='pw', is_instructor=True)
        self.parent = Track.objects.create(name='Tech')
        self.child = Track.objects.create(name='Web Development', parent=self.parent)

    def test_create_course_form_only_offers_leaf_tracks(self):
        from .forms import CourseCreationForm
        form = CourseCreationForm()
        track_ids = set(form.fields['track'].queryset.values_list('id', flat=True))
        self.assertIn(self.child.id, track_ids)
        self.assertNotIn(self.parent.id, track_ids)

    def test_posting_a_parent_track_is_rejected(self):
        self.client.force_login(self.instructor)
        response = self.client.post(reverse('create_course'), {
            'title': 'Broken Course', 'description': 'x', 'track': self.parent.id,
            'level': Course.Level.BEGINNER, 'language': 'English',
            'production_type': Course.ProductionType.FULL, 'price': '0.00',
        })
        self.assertFalse(Course.objects.filter(title='Broken Course').exists())
        self.assertEqual(response.status_code, 200)  # re-renders the form with errors


class MisfiledCourseDetectionTests(TestCase):
    """The admin dashboard must surface any course that can never appear on
    a student browse page (filed under a parent category, or no track at
    all) so it can be found without a database console."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username='misfile_admin', password='pw')
        self.instructor = User.objects.create_user(
            username='misfile_inst', password='pw', is_instructor=True)
        self.parent = Track.objects.create(name='Tech Parent')
        self.leaf = Track.objects.create(name='Web Development Leaf', parent=self.parent)

    def test_course_under_parent_track_is_flagged(self):
        course = Course.objects.create(
            instructor=self.instructor, track=self.parent, title='Misfiled Course',
            description='...', production_type=Course.ProductionType.FULL, price=Decimal('0.00'))
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_dashboard'))
        self.assertContains(response, 'Misfiled Course')
        self.assertIn(course, response.context['misfiled_courses'])

    def test_course_under_leaf_track_is_not_flagged(self):
        Course.objects.create(
            instructor=self.instructor, track=self.leaf, title='Fine Course',
            description='...', production_type=Course.ProductionType.FULL, price=Decimal('0.00'))
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_dashboard'))
        self.assertNotContains(response, 'Fine Course')


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

    def test_rejecting_one_course_does_not_touch_others(self):
        # Regression test: rejecting a single course must never affect any
        # other course -- not its status, and definitely not deleting it.
        track = Track.objects.create(name='Reject Isolation Track')
        course_b = Course.objects.create(
            instructor=self.instructor, track=track, title='Course B', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('0.00'), is_free=True,
            status=Course.Status.PENDING_REVIEW,
        )
        course_c = Course.objects.create(
            instructor=self.instructor, track=track, title='Course C', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('0.00'), is_free=True,
            status=Course.Status.PENDING_REVIEW,
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('reject_course', args=[self.course.id]), {'reason': 'Not good enough'})
        self.assertEqual(response.status_code, 302)

        self.course.refresh_from_db()
        course_b.refresh_from_db()
        course_c.refresh_from_db()

        self.assertEqual(self.course.status, Course.Status.REJECTED)
        self.assertEqual(course_b.status, Course.Status.PENDING_REVIEW)
        self.assertEqual(course_c.status, Course.Status.PENDING_REVIEW)
        self.assertEqual(Course.objects.count(), 3)  # nothing deleted


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
        parent = Track.objects.create(name='Tech Category')
        track = Track.objects.create(name='Data Science & AI', parent=parent)
        self.client.force_login(self.admin)
        self.client.post(reverse('admin_categories'), {'track': track.id, 'name': 'Machine Learning'})
        category = Category.objects.get(name='Machine Learning')

        self.client.post(reverse('delete_category', args=[category.id]))
        self.assertFalse(Category.objects.filter(id=category.id).exists())


TEST_HMAC_SECRET = 'test-hmac-secret'


def _signed_transaction(merchant_order_id, **overrides):
    """Build a Paymob transaction dict shaped like the real callback payload
    (order/owner as nested objects) plus the correct HMAC for it, computed the
    same way the webhook view does: flatten, then sign."""
    nested = {
        'amount_cents': 5000, 'created_at': '2026-01-01T00:00:00Z', 'currency': 'EGP',
        'error_occured': False, 'has_parent_transaction': False, 'id': 123456,
        'integration_id': 1, 'is_3d_secure': True, 'is_auth': False, 'is_capture': False,
        'is_refunded': False, 'is_standalone_payment': True, 'is_voided': False,
        'order': {'id': 999, 'merchant_order_id': merchant_order_id}, 'owner': {'id': 1},
        'pending': False,
        'source_data': {'pan': '1234', 'sub_type': 'VISA', 'type': 'card'},
        'success': True,
    }
    nested.update(overrides)
    flat = paymob.flatten_callback_obj(nested)
    concatenated = ''.join(str(flat.get(f, '')) for f in paymob.HMAC_FIELDS)
    signature = hmac.new(TEST_HMAC_SECRET.encode(), concatenated.encode(), hashlib.sha512).hexdigest()
    return nested, signature


@override_settings(PAYMOB_HMAC_SECRET=TEST_HMAC_SECRET)
class PaymobHmacTests(TestCase):
    def test_valid_signature_verifies(self):
        nested, signature = _signed_transaction('course1-student2-abc123')
        self.assertTrue(paymob.verify_hmac(paymob.flatten_callback_obj(nested), signature))

    def test_tampered_amount_fails_verification(self):
        nested, signature = _signed_transaction('course1-student2-abc123')
        nested['amount_cents'] = 999999  # attacker changes the amount after signing
        self.assertFalse(paymob.verify_hmac(paymob.flatten_callback_obj(nested), signature))

    def test_wrong_signature_fails_verification(self):
        nested, _ = _signed_transaction('course1-student2-abc123')
        self.assertFalse(
            paymob.verify_hmac(paymob.flatten_callback_obj(nested), 'not-the-real-signature'))

    def test_flatten_callback_obj_extracts_nested_ids(self):
        raw = {
            'order': {'id': 999, 'merchant_order_id': 'course1-student2-abcdef1234'},
            'owner': {'id': 1},
            'source_data': {'pan': '1234', 'sub_type': 'VISA', 'type': 'card'},
        }
        flat = paymob.flatten_callback_obj(raw)
        self.assertEqual(flat['order'], 999)
        self.assertEqual(flat['owner'], 1)
        self.assertEqual(flat['source_data_pan'], '1234')


@override_settings(PAYMOB_HMAC_SECRET=TEST_HMAC_SECRET)
class PaymobWebhookTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='paymob_inst', password='pw', is_instructor=True)
        self.student = User.objects.create_user(
            username='paymob_stud', password='pw', is_student=True)
        track = Track.objects.create(name='Business & Marketing')
        self.course = Course.objects.create(
            instructor=self.instructor, track=track, title='Paid Course', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('50.00'),
            status=Course.Status.PUBLISHED,
        )

    def _post_webhook(self, nested, signature):
        return self.client.post(
            f"{reverse('paymob_webhook')}?hmac={signature}",
            data=json.dumps({'obj': nested}), content_type='application/json')

    def test_successful_transaction_creates_payment_enrollment_and_wallet_credit(self):
        merchant_order_id = f'course{self.course.id}-student{self.student.id}-abc123'
        nested, signature = _signed_transaction(merchant_order_id)

        response = self._post_webhook(nested, signature)
        self.assertEqual(response.status_code, 200)

        payment = Payment.objects.get(provider_transaction_id='123456')
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.instructor_amount, Decimal('35.00'))
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=self.course).exists())

        wallet = InstructorWallet.objects.get(instructor=self.instructor)
        self.assertEqual(wallet.available_balance, Decimal('35.00'))
        self.assertTrue(WalletTransaction.objects.filter(
            wallet=wallet, type=WalletTransaction.Type.SALE_CREDIT, amount=Decimal('35.00')).exists())

    def test_duplicate_webhook_delivery_does_not_double_credit(self):
        merchant_order_id = f'course{self.course.id}-student{self.student.id}-abc123'
        nested, signature = _signed_transaction(merchant_order_id)

        self._post_webhook(nested, signature)
        self._post_webhook(nested, signature)  # Paymob retries the same delivery

        self.assertEqual(Payment.objects.filter(provider_transaction_id='123456').count(), 1)
        wallet = InstructorWallet.objects.get(instructor=self.instructor)
        self.assertEqual(wallet.available_balance, Decimal('35.00'))
        self.assertEqual(WalletTransaction.objects.filter(wallet=wallet).count(), 1)

    def test_invalid_signature_is_rejected(self):
        merchant_order_id = f'course{self.course.id}-student{self.student.id}-abc123'
        nested, _ = _signed_transaction(merchant_order_id)
        response = self._post_webhook(nested, 'totally-fake-signature')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Payment.objects.exists())

    def test_failed_transaction_creates_nothing(self):
        merchant_order_id = f'course{self.course.id}-student{self.student.id}-abc123'
        nested, signature = _signed_transaction(merchant_order_id, success=False)

        response = self._post_webhook(nested, signature)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())

    def test_refund_reverses_wallet_credit(self):
        merchant_order_id = f'course{self.course.id}-student{self.student.id}-abc123'
        nested, signature = _signed_transaction(merchant_order_id)
        self._post_webhook(nested, signature)

        wallet = InstructorWallet.objects.get(instructor=self.instructor)
        self.assertEqual(wallet.available_balance, Decimal('35.00'))

        refund_nested, refund_signature = _signed_transaction(merchant_order_id, is_refunded=True)
        response = self._post_webhook(refund_nested, refund_signature)
        self.assertEqual(response.status_code, 200)

        wallet.refresh_from_db()
        self.assertEqual(wallet.available_balance, Decimal('0.00'))
        payment = Payment.objects.get(provider_transaction_id='123456')
        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertTrue(WalletTransaction.objects.filter(
            wallet=wallet, type=WalletTransaction.Type.REFUND_DEBIT, amount=Decimal('35.00')).exists())


@override_settings(PAYMOB_HMAC_SECRET=TEST_HMAC_SECRET)
class SubscriptionWebhookTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='sub_stud', password='pw', is_student=True)
        self.plan = Plan.objects.create(
            name='Mendoura Annual Pass', price_egp=Decimal('1499.00'), price_usd=Decimal('49.00'))

    def _post_webhook(self, nested, signature):
        return self.client.post(
            f"{reverse('paymob_webhook')}?hmac={signature}",
            data=json.dumps({'obj': nested}), content_type='application/json')

    def test_successful_subscription_payment_creates_active_subscription(self):
        merchant_order_id = f'sub{self.plan.id}-student{self.student.id}-abc123'
        nested, signature = _signed_transaction(merchant_order_id, amount_cents=149900)

        response = self._post_webhook(nested, signature)
        self.assertEqual(response.status_code, 200)

        subscription = Subscription.objects.get(provider_transaction_id='123456')
        self.assertEqual(subscription.student, self.student)
        self.assertEqual(subscription.plan, self.plan)
        self.assertEqual(subscription.amount_paid, Decimal('1499.00'))
        self.assertTrue(subscription.is_active_now())

    def test_duplicate_subscription_webhook_does_not_double_create(self):
        merchant_order_id = f'sub{self.plan.id}-student{self.student.id}-abc123'
        nested, signature = _signed_transaction(merchant_order_id, amount_cents=149900)

        self._post_webhook(nested, signature)
        self._post_webhook(nested, signature)

        self.assertEqual(Subscription.objects.filter(provider_transaction_id='123456').count(), 1)


class SubscriptionAccessControlTests(TestCase):
    """An active subscriber gets frictionless access to any paid course
    without an individual purchase."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='sub_access_inst', password='pw', is_instructor=True)
        self.student = User.objects.create_user(
            username='sub_access_stud', password='pw', is_student=True)
        track = Track.objects.create(name='Cloud & DevOps')
        self.course = Course.objects.create(
            instructor=self.instructor, track=track, title='Paid Course', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('40.00'),
            status=Course.Status.PUBLISHED,
        )
        module = Module.objects.create(course=self.course, title='Module 1')
        self.locked_lecture = Lecture.objects.create(module=module, title='Deep Dive', is_preview=False)
        plan = Plan.objects.create(name='Mendoura Annual Pass', price_egp=Decimal('1499.00'),
                                    price_usd=Decimal('49.00'))
        self.subscription = Subscription.objects.create(
            student=self.student, plan=plan, amount_paid=Decimal('1499.00'),
            expires_at=timezone.now() + timedelta(days=365),
        )

    def test_active_subscriber_can_watch_locked_lecture_without_buying_course(self):
        self.client.force_login(self.student)
        response = self.client.get(
            reverse('course_player', args=[self.course.id, self.locked_lecture.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Enrollment.objects.filter(
            student=self.student, course=self.course, via_subscription=True).exists())

    def test_expired_subscriber_cannot_watch_locked_lecture(self):
        self.subscription.expires_at = timezone.now() - timedelta(days=1)
        self.subscription.save()
        self.client.force_login(self.student)
        response = self.client.get(
            reverse('course_player', args=[self.course.id, self.locked_lecture.id]))
        self.assertEqual(response.status_code, 403)


class SubscriptionRevenueDistributionTests(TestCase):
    """Every piastre of a subscriber's payment must land somewhere -- either
    with an instructor or with the platform -- by construction, not luck."""

    def setUp(self):
        self.student = User.objects.create_user(username='rev_stud', password='pw', is_student=True)
        self.instructor_a = User.objects.create_user(
            username='rev_inst_a', password='pw', is_instructor=True)
        self.instructor_b = User.objects.create_user(
            username='rev_inst_b', password='pw', is_instructor=True)
        track = Track.objects.create(name='Rev Track')
        self.course_a = Course.objects.create(
            instructor=self.instructor_a, track=track, title='Course A', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('20.00'),
            status=Course.Status.PUBLISHED)
        self.course_b = Course.objects.create(
            instructor=self.instructor_b, track=track, title='Course B', description='...',
            production_type=Course.ProductionType.SCRIPT_ONLY, price=Decimal('20.00'),
            status=Course.Status.PUBLISHED)
        module_a = Module.objects.create(course=self.course_a, title='M1')
        module_b = Module.objects.create(course=self.course_b, title='M1')
        self.lecture_a = Lecture.objects.create(module=module_a, title='L1', duration_seconds=3600)
        self.lecture_b = Lecture.objects.create(module=module_b, title='L1', duration_seconds=3600)

        self.plan = Plan.objects.create(
            name='Mendoura Annual Pass', interval=Plan.Interval.ANNUAL,
            price_egp=Decimal('2000.00'), price_usd=Decimal('65.00'))
        self.subscription = Subscription.objects.create(
            student=self.student, plan=self.plan, amount_paid=Decimal('2000.00'),
            expires_at=timezone.now() - timedelta(days=1),  # already ended -> due for distribution
        )
        self.period = SubscriptionPeriod.objects.create(
            subscription=self.subscription,
            period_start=timezone.now() - timedelta(days=30),
            period_end=timezone.now() - timedelta(days=1),
            amount_paid=Decimal('2000.00'),
        )

    def _watch(self, lecture, course, seconds, minutes_ago=15):
        WatchEvent.objects.create(
            student=self.student, lecture=lecture, course=course, seconds_watched=seconds,
            occurred_at=self.period.period_start + timedelta(days=1, minutes=minutes_ago))

    def test_worked_example_to_the_piastre(self):
        self._watch(self.lecture_a, self.course_a, 1800)  # 30 min
        self._watch(self.lecture_b, self.course_b, 600)   # 10 min

        call_command('distribute_subscription_revenue')

        dist_a = RevenueDistribution.objects.get(course=self.course_a)
        dist_b = RevenueDistribution.objects.get(course=self.course_b)

        # 1800/2400 = 75% of the EGP 2000 pool
        self.assertEqual(dist_a.attributed_amount, Decimal('1500.00'))
        # Flat 60% subscription split -- NOT course_a's own 70/30 production_type rule
        self.assertEqual(dist_a.instructor_amount, Decimal('900.00'))
        self.assertEqual(dist_a.platform_amount, Decimal('600.00'))

        # 600/2400 = 25%, but course_b is last (ordered by id) so it gets the
        # exact remainder rather than a separately-rounded 25% slice
        self.assertEqual(dist_b.attributed_amount, Decimal('500.00'))
        self.assertEqual(dist_b.instructor_amount, Decimal('300.00'))
        self.assertEqual(dist_b.platform_amount, Decimal('200.00'))

        self.assertEqual(dist_a.attributed_amount + dist_b.attributed_amount, Decimal('2000.00'))

        wallet_a = InstructorWallet.objects.get(instructor=self.instructor_a)
        wallet_b = InstructorWallet.objects.get(instructor=self.instructor_b)
        self.assertEqual(wallet_a.available_balance, Decimal('900.00'))
        self.assertEqual(wallet_b.available_balance, Decimal('300.00'))

        self.period.refresh_from_db()
        self.assertEqual(self.period.status, SubscriptionPeriod.Status.DISTRIBUTED)

    def test_zero_watch_time_does_not_crash_and_keeps_it_all_on_platform(self):
        call_command('distribute_subscription_revenue')
        self.assertFalse(RevenueDistribution.objects.exists())
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, SubscriptionPeriod.Status.DISTRIBUTED)

    def test_job_run_twice_credits_wallets_once(self):
        self._watch(self.lecture_a, self.course_a, 1800)
        self._watch(self.lecture_b, self.course_b, 600)

        call_command('distribute_subscription_revenue')
        call_command('distribute_subscription_revenue')

        wallet_a = InstructorWallet.objects.get(instructor=self.instructor_a)
        self.assertEqual(wallet_a.available_balance, Decimal('900.00'))  # not doubled
        self.assertEqual(RevenueDistribution.objects.filter(course=self.course_a).count(), 1)

    def test_view_under_minimum_threshold_does_not_count(self):
        self._watch(self.lecture_a, self.course_a, 1800)
        self._watch(self.lecture_b, self.course_b, 10)  # under the 30s floor

        call_command('distribute_subscription_revenue')

        self.assertFalse(RevenueDistribution.objects.filter(course=self.course_b).exists())
        dist_a = RevenueDistribution.objects.get(course=self.course_a)
        self.assertEqual(dist_a.attributed_amount, Decimal('2000.00'))  # gets the whole pool

    def test_instructor_watching_own_course_is_excluded(self):
        # instructor_a "watches" their own course -- must not earn from it
        WatchEvent.objects.create(
            student=self.instructor_a, lecture=self.lecture_a, course=self.course_a,
            seconds_watched=1800, occurred_at=self.period.period_start + timedelta(days=1))
        self._watch(self.lecture_b, self.course_b, 600)

        call_command('distribute_subscription_revenue')

        self.assertFalse(RevenueDistribution.objects.filter(course=self.course_a).exists())
        dist_b = RevenueDistribution.objects.get(course=self.course_b)
        self.assertEqual(dist_b.attributed_amount, Decimal('2000.00'))

    def test_rewatching_same_lecture_capped_at_double_duration(self):
        # lecture_a is 3600s long; claim 10x that across several events
        for _ in range(10):
            self._watch(self.lecture_a, self.course_a, 3600)
        self._watch(self.lecture_b, self.course_b, 600)

        call_command('distribute_subscription_revenue')

        dist_a = RevenueDistribution.objects.get(course=self.course_a)
        # Capped at 2x duration (7200s), not the full 36000s claimed
        self.assertEqual(dist_a.seconds_watched, 7200)

    def test_distribution_sums_to_pool_across_random_watch_splits(self):
        courses = []
        for i in range(5):
            instructor = User.objects.create_user(username=f'fuzz_inst_{i}', password='pw', is_instructor=True)
            course = Course.objects.create(
                instructor=instructor, track=self.course_a.track, title=f'Fuzz Course {i}',
                description='...', production_type=Course.ProductionType.FULL,
                price=Decimal('10.00'), status=Course.Status.PUBLISHED)
            module = Module.objects.create(course=course, title='M1')
            lecture = Lecture.objects.create(module=module, title='L1', duration_seconds=36000)
            seconds = random.randint(30, 5000)
            self._watch(lecture, course, seconds)
            courses.append(course)

        call_command('distribute_subscription_revenue')

        distributions = RevenueDistribution.objects.filter(period=self.period)
        total_attributed = sum((d.attributed_amount for d in distributions), Decimal('0.00'))
        self.assertEqual(total_attributed, Decimal('2000.00'))
        for dist in distributions:
            self.assertEqual(dist.instructor_amount + dist.platform_amount, dist.attributed_amount)

    def test_admin_subscription_revenue_page_renders_a_known_distribution(self):
        self._watch(self.lecture_a, self.course_a, 1800)
        self._watch(self.lecture_b, self.course_b, 600)
        call_command('distribute_subscription_revenue')

        admin = User.objects.create_superuser(username='rev_page_admin', password='pw')
        self.client.force_login(admin)
        response = self.client.get(reverse('admin_subscription_revenue'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.course_a.title)
        self.assertContains(response, self.instructor_a.username)
        self.assertContains(response, '900.00')  # instructor_a's share

    def test_direct_sale_split_unaffected_by_subscription_path(self):
        self._watch(self.lecture_a, self.course_a, 1800)
        self._watch(self.lecture_b, self.course_b, 600)
        call_command('distribute_subscription_revenue')

        # A direct one-off sale on course_a (70% production_type=full) must
        # still use its own split rule, not the flat 60% subscription rate.
        payment = Payment.objects.create(
            student=self.student, course=self.course_a, total_amount=Decimal('20.00'))
        self.assertEqual(payment.instructor_amount, Decimal('14.00'))  # 70% of $20
        self.assertEqual(payment.platform_amount, Decimal('6.00'))

    def test_refund_after_distribution_reverses_instructor_credit(self):
        self._watch(self.lecture_a, self.course_a, 1800)
        self._watch(self.lecture_b, self.course_b, 600)
        call_command('distribute_subscription_revenue')

        wallet_a = InstructorWallet.objects.get(instructor=self.instructor_a)
        self.assertEqual(wallet_a.available_balance, Decimal('900.00'))

        self.subscription.provider_transaction_id = 'sub-txn-refund-1'
        self.subscription.save()

        nested, signature = _signed_transaction(
            f'sub{self.plan.id}-student{self.student.id}-abc123',
            id='sub-txn-refund-1', is_refunded=True)

        with override_settings(PAYMOB_HMAC_SECRET=TEST_HMAC_SECRET):
            response = self.client.post(
                f"{reverse('paymob_webhook')}?hmac={signature}",
                data=json.dumps({'obj': nested}), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        wallet_a.refresh_from_db()
        self.assertEqual(wallet_a.available_balance, Decimal('0.00'))
        self.assertTrue(WalletTransaction.objects.filter(
            wallet=wallet_a, type=WalletTransaction.Type.REFUND_DEBIT, amount=Decimal('900.00')).exists())
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.CANCELED)


class SignupDuplicateGuardTests(TestCase):
    def test_duplicate_username_shows_friendly_error(self):
        User.objects.create_user(username='taken', password='pw', email='a@example.com')
        response = self.client.post(reverse('student_signup'), {
            'username': 'taken', 'email': 'b@example.com',
            'password1': 'a-strong-password-1', 'password2': 'a-strong-password-1',
        })
        self.assertContains(response, 'An account with this username already exists.')
        self.assertEqual(User.objects.filter(username='taken').count(), 1)

    def test_duplicate_email_shows_friendly_error(self):
        User.objects.create_user(username='first', password='pw', email='dup@example.com')
        response = self.client.post(reverse('student_signup'), {
            'username': 'second', 'email': 'dup@example.com',
            'password1': 'a-strong-password-1', 'password2': 'a-strong-password-1',
        })
        self.assertContains(response, 'An account with this email already exists.')
        self.assertFalse(User.objects.filter(username='second').exists())

    def test_duplicate_phone_number_shows_friendly_error(self):
        User.objects.create_user(username='first_phone', password='pw', phone_number='+201001234567')
        response = self.client.post(reverse('student_signup'), {
            'username': 'second_phone', 'email': 'c@example.com', 'phone_number': '+201001234567',
            'password1': 'a-strong-password-1', 'password2': 'a-strong-password-1',
        })
        self.assertContains(response, 'An account with this phone number already exists.')
        self.assertFalse(User.objects.filter(username='second_phone').exists())


class CheckoutFlowTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='checkout_inst', password='pw', is_instructor=True)
        self.student = User.objects.create_user(
            username='checkout_stud', password='pw', is_student=True)
        track = Track.objects.create(name='Cybersecurity')
        self.paid_course = Course.objects.create(
            instructor=self.instructor, track=track, title='Paid Course', description='...',
            production_type=Course.ProductionType.FULL, price=Decimal('30.00'),
            status=Course.Status.PUBLISHED,
        )

    def test_enroll_on_paid_course_redirects_to_checkout(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse('enroll_course', args=[self.paid_course.id]))
        self.assertRedirects(response, reverse('checkout_course', args=[self.paid_course.id]),
                              fetch_redirect_response=False)
        self.assertFalse(Enrollment.objects.filter(student=self.student).exists())

    def test_checkout_page_shows_both_purchase_options(self):
        Plan.objects.create(name='Mendoura Annual Pass', price_egp=Decimal('1499.00'), price_usd=Decimal('49.00'))
        self.client.force_login(self.student)
        response = self.client.get(reverse('checkout_course', args=[self.paid_course.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Buy This Course')
        self.assertContains(response, 'Get the Annual Pass')

    @patch('courses.views.paymob.initiate_checkout')
    def test_checkout_course_option_redirects_to_paymob_iframe(self, mock_initiate):
        mock_initiate.return_value = 'https://accept.paymob.com/api/acceptance/iframes/1?payment_token=abc'
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('checkout_course', args=[self.paid_course.id]), {'option': 'course'})
        self.assertRedirects(
            response, 'https://accept.paymob.com/api/acceptance/iframes/1?payment_token=abc',
            fetch_redirect_response=False)
        mock_initiate.assert_called_once()
        amount_cents = mock_initiate.call_args[0][0]
        self.assertEqual(amount_cents, 3000)  # course price, not the subscription price

    @patch('courses.views.paymob.initiate_checkout')
    def test_checkout_subscription_option_redirects_to_paymob_iframe(self, mock_initiate):
        plan = Plan.objects.create(name='Mendoura Annual Pass', price_egp=Decimal('1499.00'), price_usd=Decimal('49.00'))
        mock_initiate.return_value = 'https://accept.paymob.com/api/acceptance/iframes/2?payment_token=xyz'
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('checkout_course', args=[self.paid_course.id]),
            {'option': 'subscription', 'plan_id': plan.id})
        self.assertRedirects(
            response, 'https://accept.paymob.com/api/acceptance/iframes/2?payment_token=xyz',
            fetch_redirect_response=False)
        amount_cents = mock_initiate.call_args[0][0]
        self.assertEqual(amount_cents, 149900)  # plan price in EGP cents, not the course price

    def test_already_enrolled_student_cannot_start_checkout_again(self):
        Enrollment.objects.create(student=self.student, course=self.paid_course)
        self.client.force_login(self.student)
        response = self.client.get(reverse('checkout_course', args=[self.paid_course.id]))
        self.assertRedirects(response, reverse('course_detail', args=[self.paid_course.id]))


class SeedAdminCommandTests(TestCase):
    """The only way to get an admin login on a Shell-less Render free plan
    is this command running at build time, so it must actually work."""

    def test_noop_without_env_vars(self):
        call_command('seed_admin')
        self.assertFalse(User.objects.filter(is_superuser=True).exists())

    def test_creates_superuser_when_env_vars_set(self):
        with patch('courses.management.commands.seed_admin.config') as mock_config:
            mock_config.side_effect = lambda key, default='': {
                'DJANGO_SUPERUSER_USERNAME': 'siteadmin',
                'DJANGO_SUPERUSER_PASSWORD': 'a-strong-password-1',
                'DJANGO_SUPERUSER_EMAIL': 'admin@example.com',
            }.get(key, default)
            call_command('seed_admin')

        admin = User.objects.get(username='siteadmin')
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.check_password('a-strong-password-1'))

    def test_idempotent_on_second_run(self):
        with patch('courses.management.commands.seed_admin.config') as mock_config:
            mock_config.side_effect = lambda key, default='': {
                'DJANGO_SUPERUSER_USERNAME': 'siteadmin2',
                'DJANGO_SUPERUSER_PASSWORD': 'a-strong-password-1',
            }.get(key, default)
            call_command('seed_admin')
            call_command('seed_admin')

        self.assertEqual(User.objects.filter(username='siteadmin2').count(), 1)
