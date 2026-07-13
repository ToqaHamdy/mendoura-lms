import json
import re
import uuid
from decimal import Decimal
from functools import wraps

import requests
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import paymob
from .forms import (
    CategoryForm, CourseCreationForm, InstructorSignUpForm, LectureForm, ModuleForm,
    PayoutRequestForm, ResourceForm, ReviewForm, StudentSignUpForm, TrackForm,
)
from .models import (
    Category, Certificate, Course, Enrollment, InstructorWallet, Lecture, LectureProgress,
    Module, Payment, Payout, Resource, Review, Track, User, WalletTransaction,
)


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            return redirect('platform_home')
        return view_func(request, *args, **kwargs)
    return wrapper

# 1. Platform Homepage
def platform_home(request):
    tracks = Track.objects.filter(is_active=True)[:8]
    return render(request, 'platform_home.html', {'tracks': tracks})

# 2. Student Sign Up View
def student_signup(request):
    if request.method == 'POST':
        form = StudentSignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('platform_home')
    else:
        form = StudentSignUpForm()
    return render(request, 'registration/signup_student.html', {'form': form})

# 3. Instructor Sign Up View
def instructor_signup(request):
    if request.method == 'POST':
        form = InstructorSignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('instructor_dashboard')
    else:
        form = InstructorSignUpForm()
    return render(request, 'registration/signup_instructor.html', {'form': form})

# 4. Instructor Dashboard View
@login_required
def instructor_dashboard(request):
    if not request.user.is_instructor:
        return redirect('platform_home')
    courses = Course.objects.filter(instructor=request.user).order_by('-created_at')
    wallet, _ = InstructorWallet.objects.get_or_create(instructor=request.user)
    recent_sales = (Payment.objects.filter(course__instructor=request.user,
                                            status=Payment.Status.SUCCEEDED)
                     .select_related('course', 'student').order_by('-created_at')[:5])
    total_students = Enrollment.objects.filter(course__instructor=request.user).count()

    return render(request, 'dashboard/instructor.html', {
        'courses': courses,
        'wallet': wallet,
        'recent_sales': recent_sales,
        'total_students': total_students,
    })

# 5. Create Course View -- production_type is chosen here and is read-only
# once the course has its first successful sale (enforced in Course.save()).
@login_required
def create_course(request):
    if not request.user.is_instructor:
        return redirect('platform_home')
    if request.method == 'POST':
        form = CourseCreationForm(request.POST, request.FILES)
        if form.is_valid():
            course = form.save(commit=False)
            course.instructor = request.user
            course.save()
            return redirect('manage_modules', course_id=course.id)
    else:
        form = CourseCreationForm()
    return render(request, 'dashboard/create_course.html', {'form': form})

# 6. Course Catalog - Browse all published courses
def course_catalog(request):
    courses = Course.objects.filter(status=Course.Status.PUBLISHED).order_by('-created_at')
    return render(request, 'courses/catalog.html', {'courses': courses})

# 7. Course Detail - View a single course + its curriculum
def course_detail(request, course_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    modules = course.modules.prefetch_related('lectures').order_by('order')
    reviews = course.reviews.select_related('student').order_by('-created_at')

    enrollment = None
    user_review = None
    if request.user.is_authenticated:
        enrollment = Enrollment.objects.filter(student=request.user, course=course).first()
        user_review = reviews.filter(student=request.user).first()

    can_review = bool(
        request.user.is_authenticated and request.user.is_student
        and enrollment is not None and user_review is None
    )

    return render(request, 'courses/detail.html', {
        'course': course,
        'modules': modules,
        'reviews': reviews,
        'enrollment': enrollment,
        'can_review': can_review,
        'review_form': ReviewForm() if can_review else None,
    })


# Enroll in a free course instantly. Paid courses go through checkout_course instead.
@login_required
def enroll_course(request, course_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    if not request.user.is_student:
        return redirect('course_detail', course_id=course.id)

    if Enrollment.objects.filter(student=request.user, course=course).exists():
        return redirect('course_detail', course_id=course.id)

    if course.is_free or course.price == 0:
        Enrollment.objects.create(student=request.user, course=course)
        messages.success(request, f'You are now enrolled in {course.title}.')
        return redirect('my_learning')

    return redirect('checkout_course', course_id=course.id)


# Start a Paymob checkout for a paid course. This only redirects the student
# to Paymob's hosted iframe -- it must NOT create the Payment/Enrollment, since
# a browser reaching this view is not proof of payment. That happens in the
# webhook once Paymob confirms the transaction succeeded.
@login_required
def checkout_course(request, course_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    if not request.user.is_student or course.is_free or course.price == 0:
        return redirect('course_detail', course_id=course.id)
    if Enrollment.objects.filter(student=request.user, course=course).exists():
        return redirect('course_detail', course_id=course.id)

    merchant_order_id = f'course{course.id}-student{request.user.id}-{uuid.uuid4().hex[:10]}'
    amount_cents = int(course.price * 100)
    billing_data = {
        'first_name': request.user.first_name or request.user.username,
        'last_name': request.user.last_name or 'Student',
        'email': request.user.email or f'{request.user.username}@example.com',
        'phone_number': request.user.phone_number or 'NA',
        'country': 'EG', 'city': 'NA', 'state': 'NA',
        'street': 'NA', 'building': 'NA', 'floor': 'NA', 'apartment': 'NA',
    }
    try:
        checkout_url = paymob.initiate_checkout(amount_cents, merchant_order_id, billing_data)
    except requests.RequestException:
        messages.error(request, 'Unable to start checkout right now. Please try again shortly.')
        return redirect('course_detail', course_id=course.id)

    return redirect(checkout_url)


# Paymob webhook -- this is what actually creates the Payment + Enrollment +
# wallet credit. Idempotent: the unique constraint on provider_transaction_id
# means a retried webhook can't double-credit a wallet.
@csrf_exempt
def paymob_webhook(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)

    obj = payload.get('obj', {})
    received_hmac = request.GET.get('hmac', '')
    if not paymob.verify_hmac(paymob.flatten_callback_obj(obj), received_hmac):
        return HttpResponse(status=403)

    if not obj.get('success'):
        return HttpResponse(status=200)  # nothing to do for a failed/pending transaction

    transaction_id = str(obj.get('id'))
    order = obj.get('order') or {}
    merchant_order_id = order.get('merchant_order_id', '') if isinstance(order, dict) else ''
    match = re.match(r'course(\d+)-student(\d+)-', merchant_order_id)
    if not match:
        return HttpResponse(status=400)
    course_id, student_id = int(match.group(1)), int(match.group(2))

    try:
        with transaction.atomic():
            course = Course.objects.select_related('instructor').get(id=course_id)
            student = User.objects.get(id=student_id)
            amount = Decimal(str(obj.get('amount_cents', 0))) / Decimal('100')

            if obj.get('is_refunded'):
                _process_refund(transaction_id)
                return HttpResponse(status=200)

            payment, created = Payment.objects.get_or_create(
                provider_transaction_id=transaction_id,
                defaults={
                    'student': student, 'course': course, 'total_amount': amount,
                    'status': Payment.Status.SUCCEEDED,
                },
            )
            if created:
                wallet, _ = InstructorWallet.objects.get_or_create(instructor=course.instructor)
                wallet = InstructorWallet.objects.select_for_update().get(pk=wallet.pk)
                wallet.available_balance += payment.instructor_amount
                wallet.total_earnings += payment.instructor_amount
                wallet.save()
                WalletTransaction.objects.create(
                    wallet=wallet, type=WalletTransaction.Type.SALE_CREDIT,
                    amount=payment.instructor_amount, balance_after=wallet.available_balance,
                    payment=payment)
                Enrollment.objects.get_or_create(
                    student=student, course=course, defaults={'payment': payment})
    except IntegrityError:
        pass  # duplicate webhook delivery for a transaction we've already processed

    return HttpResponse(status=200)


def _process_refund(transaction_id):
    payment = Payment.objects.filter(
        provider_transaction_id=transaction_id, status=Payment.Status.SUCCEEDED).first()
    if payment is None:
        return
    payment.status = Payment.Status.REFUNDED
    payment.save()

    wallet = InstructorWallet.objects.select_for_update().get(instructor=payment.course.instructor)
    wallet.available_balance -= payment.instructor_amount
    wallet.save()
    WalletTransaction.objects.create(
        wallet=wallet, type=WalletTransaction.Type.REFUND_DEBIT,
        amount=payment.instructor_amount, balance_after=wallet.available_balance,
        payment=payment, note=f'Refund for transaction {transaction_id}')


# Leave a review -- enrolled students only, one review per student per course.
@login_required
def add_review(request, course_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    is_enrolled = Enrollment.objects.filter(student=request.user, course=course).exists()
    already_reviewed = Review.objects.filter(student=request.user, course=course).exists()

    if request.method == 'POST' and is_enrolled and not already_reviewed:
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.student = request.user
            review.course = course
            review.save()

    return redirect('course_detail', course_id=course.id)


# My Learning - enrolled courses with progress
@login_required
def my_learning(request):
    enrollments = (Enrollment.objects.filter(student=request.user)
                   .select_related('course').order_by('-enrolled_at'))
    return render(request, 'courses/my_learning.html', {'enrollments': enrollments})


# Course Player - watch a lecture. Preview lectures are open to anyone;
# everything else requires an active enrollment.
def course_player(request, course_id, lecture_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    lecture = get_object_or_404(Lecture, id=lecture_id, module__course=course)

    enrollment = None
    if request.user.is_authenticated:
        enrollment = Enrollment.objects.filter(student=request.user, course=course).first()

    if enrollment is None and not lecture.is_preview:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return HttpResponseForbidden('Enroll in this course to watch this lecture.')

    modules = course.modules.prefetch_related('lectures').order_by('order')
    all_lectures = list(Lecture.objects.filter(module__course=course).order_by('module__order', 'order'))
    index = next((i for i, l in enumerate(all_lectures) if l.id == lecture.id), 0)
    prev_lecture = all_lectures[index - 1] if index > 0 else None
    next_lecture = all_lectures[index + 1] if index < len(all_lectures) - 1 else None

    progress = None
    if enrollment is not None:
        progress = LectureProgress.objects.filter(enrollment=enrollment, lecture=lecture).first()

    return render(request, 'courses/player.html', {
        'course': course,
        'lecture': lecture,
        'modules': modules,
        'enrollment': enrollment,
        'progress': progress,
        'prev_lecture': prev_lecture,
        'next_lecture': next_lecture,
    })


# Mark a lecture complete for the current student's enrollment
@login_required
def mark_lecture_complete(request, course_id, lecture_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    lecture = get_object_or_404(Lecture, id=lecture_id, module__course=course)
    enrollment = get_object_or_404(Enrollment, student=request.user, course=course)

    if request.method == 'POST':
        progress, _ = LectureProgress.objects.get_or_create(enrollment=enrollment, lecture=lecture)
        progress.completed = True
        progress.completed_at = timezone.now()
        progress.save()
        enrollment.issue_certificate_if_complete()

    return redirect('course_player', course_id=course.id, lecture_id=lecture.id)


# Public certificate verification page
def certificate_view(request, certificate_uuid):
    certificate = get_object_or_404(Certificate, uuid=certificate_uuid)
    return render(request, 'courses/certificate.html', {'certificate': certificate})


# Browse all active Tracks
def track_list(request):
    tracks = Track.objects.filter(is_active=True)
    return render(request, 'courses/track_list.html', {'tracks': tracks})


# A Track's published courses
def track_detail(request, slug):
    track = get_object_or_404(Track, slug=slug, is_active=True)
    courses = Course.objects.filter(track=track, status=Course.Status.PUBLISHED).order_by('-created_at')
    return render(request, 'courses/track_detail.html', {'track': track, 'courses': courses})

# 8. Submit a draft/rejected course for admin review (instructors cannot self-publish)
@login_required
def toggle_publish(request, course_id):
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    if course.status in (Course.Status.DRAFT, Course.Status.REJECTED):
        course.status = Course.Status.PENDING_REVIEW
        course.save()
    return redirect('instructor_dashboard')


# Manage a course's Modules (the sections of the curriculum)
@login_required
def manage_modules(request, course_id):
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    modules = course.modules.order_by('order')

    if request.method == 'POST':
        form = ModuleForm(request.POST)
        if form.is_valid():
            module = form.save(commit=False)
            module.course = course
            module.save()
            return redirect('manage_modules', course_id=course.id)
    else:
        form = ModuleForm()

    return render(request, 'dashboard/manage_modules.html', {
        'course': course, 'modules': modules, 'form': form,
    })


# Add lectures to a specific Module
@login_required
def manage_lectures(request, course_id, module_id):
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    module = get_object_or_404(Module, id=module_id, course=course)
    lectures = module.lectures.all()

    if request.method == 'POST':
        form = LectureForm(request.POST, request.FILES)
        if form.is_valid():
            lecture = form.save(commit=False)
            lecture.module = module
            lecture.save()
            return redirect('manage_lectures', course_id=course.id, module_id=module.id)
    else:
        form = LectureForm()

    return render(request, 'dashboard/manage_lectures.html', {
        'course': course,
        'module': module,
        'lectures': lectures,
        'form': form,
        'resource_form': ResourceForm(),
    })


# Attach a downloadable Resource to a lecture
@login_required
def add_resource(request, lecture_id):
    lecture = get_object_or_404(Lecture, id=lecture_id, module__course__instructor=request.user)
    if request.method == 'POST':
        form = ResourceForm(request.POST, request.FILES)
        if form.is_valid():
            resource = form.save(commit=False)
            resource.lecture = lecture
            if resource.file:
                resource.file_size = resource.file.size
            resource.save()
    return redirect('manage_lectures', course_id=lecture.module.course_id, module_id=lecture.module_id)


# Per-course enrolled student list
@login_required
def course_students(request, course_id):
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    enrollments = (Enrollment.objects.filter(course=course)
                   .select_related('student').order_by('-enrolled_at'))
    return render(request, 'dashboard/course_students.html', {
        'course': course, 'enrollments': enrollments,
    })


# Instructor wallet: balance summary + full transaction ledger
@login_required
def instructor_wallet(request):
    if not request.user.is_instructor:
        return redirect('platform_home')
    wallet, _ = InstructorWallet.objects.get_or_create(instructor=request.user)
    transactions = wallet.transactions.all()
    payouts = wallet.payouts.all()
    return render(request, 'dashboard/wallet.html', {
        'wallet': wallet,
        'transactions': transactions,
        'payouts': payouts,
        'form': PayoutRequestForm(),
    })


# Request a payout from available balance. The requested amount is reserved
# (moved from available_balance to pending_balance) immediately, so a second
# request can't be approved against money already promised to the first.
@login_required
def request_payout(request):
    if not request.user.is_instructor:
        return redirect('platform_home')
    wallet, _ = InstructorWallet.objects.get_or_create(instructor=request.user)

    if request.method == 'POST':
        form = PayoutRequestForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                wallet = InstructorWallet.objects.select_for_update().get(pk=wallet.pk)
                amount = form.cleaned_data['amount']
                if amount <= wallet.available_balance:
                    wallet.available_balance -= amount
                    wallet.pending_balance += amount
                    wallet.save()
                    payout = form.save(commit=False)
                    payout.wallet = wallet
                    payout.save()
                    messages.success(request, 'Payout request submitted.')
                else:
                    messages.error(request, 'Payout amount cannot exceed your available balance.')
        else:
            messages.error(request, 'Please enter a valid payout amount.')

    return redirect('instructor_wallet')

# 9. Admin Dashboard - KPIs and revenue over time
@admin_required
def admin_dashboard(request):
    succeeded_payments = Payment.objects.filter(status=Payment.Status.SUCCEEDED)
    totals = succeeded_payments.aggregate(
        total_revenue=Sum('total_amount'), platform_revenue=Sum('platform_amount'),
        instructor_revenue=Sum('instructor_amount'))
    total_paid_out = Payout.objects.filter(status=Payout.Status.PAID).aggregate(
        total=Sum('amount'))['total'] or 0

    monthly = (succeeded_payments.annotate(month=TruncMonth('created_at'))
               .values('month').annotate(revenue=Sum('total_amount')).order_by('month'))
    max_month_revenue = max([m['revenue'] for m in monthly], default=0) or 1

    context = {
        'total_students': User.objects.filter(is_student=True).count(),
        'total_instructors': User.objects.filter(is_instructor=True).count(),
        'total_courses': Course.objects.count(),
        'pending_courses_count': Course.objects.filter(status=Course.Status.PENDING_REVIEW).count(),
        'total_enrollments': Enrollment.objects.count(),
        'total_revenue': totals['total_revenue'] or 0,
        'platform_revenue': totals['platform_revenue'] or 0,
        'instructor_revenue': totals['instructor_revenue'] or 0,
        'total_paid_out': total_paid_out,
        'monthly_revenue': [
            {'label': m['month'].strftime('%b %Y'),
             'revenue': m['revenue'],
             'pct': int((m['revenue'] or 0) * 100 / max_month_revenue)}
            for m in monthly
        ],
    }
    return render(request, 'dashboard/admin.html', context)


# Course approval queue -- admin approves or rejects, instructors cannot self-publish
@admin_required
def course_approval_queue(request):
    courses = (Course.objects.filter(status=Course.Status.PENDING_REVIEW)
               .select_related('instructor', 'track').order_by('created_at'))
    return render(request, 'dashboard/course_approval_queue.html', {'courses': courses})


@admin_required
def approve_course(request, course_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PENDING_REVIEW)
    if request.method == 'POST':
        course.status = Course.Status.PUBLISHED
        course.rejection_reason = ''
        course.save()
        messages.success(request, f'{course.title} approved and published.')
    return redirect('course_approval_queue')


@admin_required
def reject_course(request, course_id):
    course = get_object_or_404(Course, id=course_id, status=Course.Status.PENDING_REVIEW)
    if request.method == 'POST':
        course.status = Course.Status.REJECTED
        course.rejection_reason = request.POST.get('reason', '')
        course.save()
        messages.success(request, f'{course.title} rejected.')
    return redirect('course_approval_queue')


# Users table, filterable by role
@admin_required
def admin_users(request):
    users = User.objects.all().order_by('-date_joined')
    role = request.GET.get('role')
    if role == 'student':
        users = users.filter(is_student=True)
    elif role == 'instructor':
        users = users.filter(is_instructor=True)
    elif role == 'admin':
        users = users.filter(is_superuser=True)
    return render(request, 'dashboard/admin_users.html', {'users': users, 'role': role})


# Payments table
@admin_required
def admin_payments(request):
    payments = Payment.objects.select_related('student', 'course').order_by('-created_at')
    return render(request, 'dashboard/admin_payments.html', {'payments': payments})


# Payout requests -- approve / reject / mark paid
@admin_required
def admin_payouts(request):
    payouts = Payout.objects.select_related('wallet__instructor').order_by('-requested_at')
    return render(request, 'dashboard/admin_payouts.html', {'payouts': payouts})


@admin_required
def approve_payout(request, payout_id):
    payout = get_object_or_404(Payout, id=payout_id, status=Payout.Status.REQUESTED)
    if request.method == 'POST':
        payout.status = Payout.Status.APPROVED
        payout.save()
    return redirect('admin_payouts')


@admin_required
def reject_payout(request, payout_id):
    payout = get_object_or_404(Payout, id=payout_id, status=Payout.Status.REQUESTED)
    if request.method == 'POST':
        with transaction.atomic():
            wallet = InstructorWallet.objects.select_for_update().get(pk=payout.wallet_id)
            wallet.pending_balance -= payout.amount
            wallet.available_balance += payout.amount
            wallet.save()
            payout.status = Payout.Status.REJECTED
            payout.admin_note = request.POST.get('admin_note', '')
            payout.processed_at = timezone.now()
            payout.save()
    return redirect('admin_payouts')


@admin_required
def mark_payout_paid(request, payout_id):
    payout = get_object_or_404(Payout, id=payout_id, status=Payout.Status.APPROVED)
    if request.method == 'POST':
        with transaction.atomic():
            wallet = InstructorWallet.objects.select_for_update().get(pk=payout.wallet_id)
            wallet.pending_balance -= payout.amount
            wallet.total_withdrawn += payout.amount
            wallet.save()
            WalletTransaction.objects.create(
                wallet=wallet, type=WalletTransaction.Type.WITHDRAWAL,
                amount=payout.amount, balance_after=wallet.available_balance,
                note=f'Payout #{payout.id}')
            payout.status = Payout.Status.PAID
            payout.processed_at = timezone.now()
            payout.save()
    return redirect('admin_payouts')


# Track & Category CRUD
@admin_required
def admin_tracks(request):
    tracks = Track.objects.order_by('order', 'name')
    if request.method == 'POST':
        form = TrackForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('admin_tracks')
    else:
        form = TrackForm()
    return render(request, 'dashboard/admin_tracks.html', {'tracks': tracks, 'form': form})


@admin_required
def toggle_track_active(request, track_id):
    track = get_object_or_404(Track, id=track_id)
    if request.method == 'POST':
        track.is_active = not track.is_active
        track.save()
    return redirect('admin_tracks')


@admin_required
def admin_categories(request):
    categories = Category.objects.select_related('track').order_by('track__name', 'name')
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('admin_categories')
    else:
        form = CategoryForm()
    return render(request, 'dashboard/admin_categories.html', {'categories': categories, 'form': form})


@admin_required
def delete_category(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    if request.method == 'POST':
        category.delete()
    return redirect('admin_categories')
