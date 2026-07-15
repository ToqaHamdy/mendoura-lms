import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from functools import wraps

import requests
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Prefetch, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import paymob
from .access import get_or_create_enrollment, student_has_access
from .forms import (
    CategoryForm, CourseCreationForm, InstructorSignUpForm, LectureForm, ModuleForm,
    PayoutRequestForm, ResourceForm, ReviewForm, StudentSignUpForm, TrackForm,
)
from .models import (
    Category, Certificate, Course, Enrollment, InstructorWallet, Lecture, LectureProgress,
    Module, Payment, Payout, Plan, Resource, RevenueDistribution, Review, Subscription,
    SubscriptionPeriod, Track, TrackRoadmapStep, User, WalletTransaction, WatchEvent,
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
    tracks = Track.objects.filter(parent__isnull=True, is_active=True)
    plans = Plan.objects.filter(is_active=True)
    return render(request, 'platform_home.html', {'tracks': tracks, 'plans': plans})

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

def _with_stats(queryset):
    """Annotate courses with a live average rating and enrolled-student count,
    for display on course cards and detail pages."""
    return queryset.annotate(avg_rating=Avg('reviews__rating'), enrolled_count=Count('enrollments'))


# 6. Course Catalog - Browse all published courses
def course_catalog(request):
    courses = _with_stats(
        Course.objects.filter(status=Course.Status.PUBLISHED)).order_by('-created_at')
    return render(request, 'courses/catalog.html', {'courses': courses})

# 7. Course Detail - View a single course + its curriculum
def course_detail(request, course_id):
    course = get_object_or_404(
        _with_stats(Course.objects.all()), id=course_id, status=Course.Status.PUBLISHED)
    modules = course.modules.prefetch_related('lectures').order_by('order')
    reviews = course.reviews.select_related('student').order_by('-created_at')

    enrollment = None
    user_review = None
    if request.user.is_authenticated:
        enrollment = get_or_create_enrollment(request.user, course)
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

    if student_has_access(request.user, course):
        # Active subscriber -- no checkout needed, just unlock the course.
        get_or_create_enrollment(request.user, course)
        messages.success(request, f'You are now enrolled in {course.title}.')
        return redirect('my_learning')

    return redirect('checkout_course', course_id=course.id)


def _paymob_billing_data(user):
    return {
        'first_name': user.first_name or user.username,
        'last_name': user.last_name or 'Student',
        'email': user.email or f'{user.username}@example.com',
        'phone_number': user.phone_number or 'NA',
        'country': 'EG', 'city': 'NA', 'state': 'NA',
        'street': 'NA', 'building': 'NA', 'floor': 'NA', 'apartment': 'NA',
    }


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

    plans = Plan.objects.filter(is_active=True)

    if request.method == 'POST':
        option = request.POST.get('option')
        plan = plans.filter(id=request.POST.get('plan_id')).first() if option == 'subscription' else None

        if plan:
            merchant_order_id = f'sub{plan.id}-student{request.user.id}-{uuid.uuid4().hex[:10]}'
            amount_cents = int(plan.price_egp * 100)
        else:
            merchant_order_id = f'course{course.id}-student{request.user.id}-{uuid.uuid4().hex[:10]}'
            amount_cents = int(course.price * 100)

        try:
            checkout_url = paymob.initiate_checkout(
                amount_cents, merchant_order_id, _paymob_billing_data(request.user))
        except requests.RequestException:
            messages.error(request, 'Unable to start checkout right now. Please try again shortly.')
            return redirect('course_detail', course_id=course.id)

        return redirect(checkout_url)

    return render(request, 'courses/checkout.html', {'course': course, 'plans': plans})


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

    course_match = re.match(r'course(\d+)-student(\d+)-', merchant_order_id)
    sub_match = re.match(r'sub(\d+)-student(\d+)-', merchant_order_id)

    if obj.get('is_refunded'):
        if sub_match:
            _process_subscription_refund(transaction_id)
        else:
            _process_refund(transaction_id)
        return HttpResponse(status=200)

    try:
        if course_match:
            _handle_course_payment(transaction_id, obj, int(course_match.group(1)), int(course_match.group(2)))
        elif sub_match:
            _handle_subscription_payment(transaction_id, obj, int(sub_match.group(1)), int(sub_match.group(2)))
        else:
            return HttpResponse(status=400)
    except IntegrityError:
        pass  # duplicate webhook delivery for a transaction we've already processed

    return HttpResponse(status=200)


def _handle_course_payment(transaction_id, obj, course_id, student_id):
    with transaction.atomic():
        course = Course.objects.select_related('instructor').get(id=course_id)
        student = User.objects.get(id=student_id)
        amount = Decimal(str(obj.get('amount_cents', 0))) / Decimal('100')

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


def _handle_subscription_payment(transaction_id, obj, plan_id, student_id):
    with transaction.atomic():
        plan = Plan.objects.get(id=plan_id)
        student = User.objects.get(id=student_id)
        amount = Decimal(str(obj.get('amount_cents', 0))) / Decimal('100')
        now = timezone.now()

        subscription, created = Subscription.objects.get_or_create(
            provider_transaction_id=transaction_id,
            defaults={
                'student': student, 'plan': plan, 'amount_paid': amount,
                'currency': 'EGP', 'expires_at': now + timedelta(days=plan.duration_days),
            },
        )
        if created:
            # One period spanning the whole subscription term -- distribution
            # (and instructor payout) happens once the period ends, not
            # re-sliced monthly even for the annual plan. See
            # SubscriptionPeriod's docstring for why.
            SubscriptionPeriod.objects.create(
                subscription=subscription, period_start=subscription.started_at,
                period_end=subscription.expires_at, amount_paid=subscription.amount_paid,
                currency=subscription.currency,
            )


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


def _process_subscription_refund(transaction_id):
    subscription = Subscription.objects.filter(provider_transaction_id=transaction_id).first()
    if subscription is None:
        return
    subscription.status = Subscription.Status.CANCELED
    subscription.save()

    period = SubscriptionPeriod.objects.filter(subscription=subscription).first()
    if period is None:
        return

    if period.status != SubscriptionPeriod.Status.DISTRIBUTED:
        # Nothing paid out yet -- just close the period so the distribution
        # job skips it.
        period.status = SubscriptionPeriod.Status.CANCELED
        period.save()
        return

    # Already distributed: reverse each instructor's credit individually.
    # Never edit the original RevenueDistribution/WalletTransaction rows --
    # the ledger is append-only, so a refund is its own new entry.
    for dist in RevenueDistribution.objects.filter(period=period).select_related('instructor'):
        wallet = InstructorWallet.objects.select_for_update().get(instructor=dist.instructor)
        wallet.available_balance -= dist.instructor_amount
        wallet.save()
        WalletTransaction.objects.create(
            wallet=wallet, type=WalletTransaction.Type.REFUND_DEBIT,
            amount=dist.instructor_amount, balance_after=wallet.available_balance,
            note=f'Refund for subscription {subscription.id}, course "{dist.course.title}"')


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

    has_access = request.user.is_authenticated and student_has_access(request.user, course)
    if not has_access and not lecture.is_preview:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return HttpResponseForbidden('Enroll in this course to watch this lecture.')

    enrollment = get_or_create_enrollment(request.user, course) if has_access else None

    modules = course.modules.prefetch_related('lectures').order_by('order')
    all_lectures = list(Lecture.objects.filter(module__course=course).order_by('module__order', 'order'))
    index = next((i for i, l in enumerate(all_lectures) if l.id == lecture.id), 0)
    prev_lecture = all_lectures[index - 1] if index > 0 else None
    next_lecture = all_lectures[index + 1] if index < len(all_lectures) - 1 else None

    progress = None
    completed_lecture_ids = set()
    if enrollment is not None:
        progress = LectureProgress.objects.filter(enrollment=enrollment, lecture=lecture).first()
        completed_lecture_ids = set(
            LectureProgress.objects.filter(enrollment=enrollment, completed=True)
            .values_list('lecture_id', flat=True))

    return render(request, 'courses/player.html', {
        'course': course,
        'lecture': lecture,
        'modules': modules,
        'enrollment': enrollment,
        'progress': progress,
        'prev_lecture': prev_lecture,
        'next_lecture': next_lecture,
        'completed_lecture_ids': completed_lecture_ids,
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


# Records a client-flushed watch-time heartbeat (aggregated client-side,
# sent roughly every 30s -- never one row per second). This is the only
# input the subscription revenue-distribution job trusts; every check here
# exists because watch-time is now money and a browser client cannot be
# trusted to report it honestly.
@login_required
def record_watch_event(request, course_id, lecture_id):
    if request.method != 'POST':
        return HttpResponse(status=405)

    course = get_object_or_404(Course, id=course_id, status=Course.Status.PUBLISHED)
    lecture = get_object_or_404(Lecture, id=lecture_id, module__course=course)

    if not student_has_access(request.user, course):
        return HttpResponseForbidden()

    try:
        seconds = int(json.loads(request.body).get('seconds', 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return HttpResponse(status=400)

    if seconds <= 0:
        return HttpResponse(status=400)

    # A single flush can't legitimately report more than the lecture's own
    # runtime plus slack for pause/seek jitter.
    if lecture.duration_seconds and seconds > lecture.duration_seconds * 1.5:
        return HttpResponse(status=400)

    last_event = WatchEvent.objects.filter(student=request.user).order_by('-occurred_at').first()
    if last_event:
        elapsed = (timezone.now() - last_event.occurred_at).total_seconds()
        # Reject a duration longer than real wall-clock time has actually
        # passed since the last heartbeat -- the strongest defense against a
        # spoofed client claiming impossible watch-time.
        if seconds > elapsed + 5:
            return HttpResponse(status=400)
        # Basic rate limit: a legitimate ~30s flush cadence can't arrive
        # faster than this.
        if elapsed < 10:
            return HttpResponse(status=429)

    WatchEvent.objects.create(student=request.user, lecture=lecture, course=course, seconds_watched=seconds)
    return HttpResponse(status=204)


# Public certificate verification page
def certificate_view(request, certificate_uuid):
    certificate = get_object_or_404(Certificate, uuid=certificate_uuid)
    return render(request, 'courses/certificate.html', {'certificate': certificate})


# Browse top-level Track categories (Tech, Languages, Marketing, Business, Design, ...)
def track_list(request):
    tracks = Track.objects.filter(parent__isnull=True, is_active=True)
    return render(request, 'courses/track_list.html', {'tracks': tracks})


def _roadmap_for_student(track, user):
    """Build the ordered roadmap steps for a leaf track, each annotated with a
    state the template can key off of: 'complete', 'in_progress', 'locked',
    'available', or 'planned' (no course linked to this step yet)."""
    steps = list(track.roadmap_steps.select_related('course').order_by('order'))
    if not user.is_authenticated:
        return [{'step': s, 'state': 'planned' if not s.course else 'available'} for s in steps]

    enrollments = {
        e.course_id: e for e in
        Enrollment.objects.filter(student=user, course__in=[s.course_id for s in steps if s.course_id])
    }
    result = []
    unlocked = True
    for s in steps:
        if not s.course:
            result.append({'step': s, 'state': 'planned'})
            continue
        enrollment = enrollments.get(s.course_id)
        if enrollment and enrollment.is_complete():
            state = 'complete'
        elif enrollment:
            state = 'in_progress'
        elif unlocked:
            state = 'available'
        else:
            state = 'locked'
        result.append({'step': s, 'state': state, 'enrollment': enrollment})
        if not (enrollment and enrollment.is_complete()) and not s.is_optional:
            unlocked = False
    return result


# A Track's published courses (leaf track) or its child tracks (parent track)
def track_detail(request, slug):
    track = get_object_or_404(
        Track.objects.select_related('parent').prefetch_related('children'),
        slug=slug, is_active=True,
    )

    if track.children.exists():
        children = track.children.filter(is_active=True)
        return render(request, 'courses/track_detail.html', {
            'track': track, 'children': children, 'is_parent': True,
        })

    courses = _with_stats(Course.objects.filter(
        track=track, status=Course.Status.PUBLISHED)).order_by('-created_at')
    roadmap = _roadmap_for_student(track, request.user)
    return render(request, 'courses/track_detail.html', {
        'track': track, 'courses': courses, 'roadmap': roadmap,
    })


# Full-text search across Tracks and Courses
def search_results(request):
    query = request.GET.get('q', '').strip()
    level = request.GET.get('level', '')
    price = request.GET.get('price', '')
    language = request.GET.get('language', '')
    track_slug = request.GET.get('track', '')

    tracks = Track.objects.none()
    courses = Course.objects.none()

    if query:
        search_query = SearchQuery(query)

        tracks = (
            Track.objects.filter(is_active=True)
            .annotate(
                search=SearchVector('name', 'description'),
                rank=SearchRank(SearchVector('name', 'description'), search_query),
            )
            .filter(search=search_query)
            .order_by('-rank')
        )

        courses = _with_stats(Course.objects.filter(status=Course.Status.PUBLISHED)).annotate(
            search=SearchVector('title', 'subtitle', 'description'),
            rank=SearchRank(SearchVector('title', 'subtitle', 'description'), search_query),
        ).filter(search=search_query)

        if level:
            courses = courses.filter(level=level)
        if price == 'free':
            courses = courses.filter(is_free=True)
        elif price == 'paid':
            courses = courses.filter(is_free=False)
        if language:
            courses = courses.filter(language__iexact=language)
        if track_slug:
            courses = courses.filter(track__slug=track_slug)

        courses = courses.order_by('-rank')

    return render(request, 'courses/search_results.html', {
        'query': query,
        'tracks': tracks,
        'courses': courses,
        'selected_level': level,
        'selected_price': price,
        'selected_language': language,
        'selected_track': track_slug,
        'levels': Course.Level.choices,
        'all_tracks': Track.objects.filter(parent__isnull=False, is_active=True).order_by('name'),
    })

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
    revenue_distributions = (
        RevenueDistribution.objects.filter(instructor=request.user)
        .select_related('course', 'period')
    )

    next_payout_available_at = None
    last_request = payouts.order_by('-requested_at').first()
    if last_request and timezone.now() - last_request.requested_at < PAYOUT_COOLDOWN:
        next_payout_available_at = last_request.requested_at + PAYOUT_COOLDOWN

    return render(request, 'dashboard/wallet.html', {
        'wallet': wallet,
        'transactions': transactions,
        'payouts': payouts,
        'revenue_distributions': revenue_distributions,
        'form': PayoutRequestForm(),
        'next_payout_available_at': next_payout_available_at,
    })


PAYOUT_COOLDOWN = timedelta(days=7)


# Request a payout from available balance. The requested amount is reserved
# (moved from available_balance to pending_balance) immediately, so a second
# request can't be approved against money already promised to the first.
@login_required
def request_payout(request):
    if not request.user.is_instructor:
        return redirect('platform_home')
    wallet, _ = InstructorWallet.objects.get_or_create(instructor=request.user)

    if request.method == 'POST':
        last_request = wallet.payouts.order_by('-requested_at').first()
        if last_request and timezone.now() - last_request.requested_at < PAYOUT_COOLDOWN:
            next_available = last_request.requested_at + PAYOUT_COOLDOWN
            messages.error(
                request,
                f'You can request a payout once a week. Next available: '
                f'{next_available.strftime("%b %d, %Y")}.')
            return redirect('instructor_wallet')

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

    # A course filed directly under a parent category (e.g. "Tech" instead
    # of "Web Development") has no course list of its own to appear in, so
    # it's silently invisible to students on every browse page. The
    # create-course form no longer allows this, but flag any course that
    # was already misfiled before that fix.
    misfiled_courses = (
        Course.objects.filter(track__parent__isnull=True)
        .select_related('track', 'instructor')
    )

    context = {
        'misfiled_courses': misfiled_courses,
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
        'due_subscription_periods_count': SubscriptionPeriod.objects.filter(
            status=SubscriptionPeriod.Status.OPEN, period_end__lte=timezone.now()).count(),
    }
    return render(request, 'dashboard/admin.html', context)


# Manually kicks off the subscription revenue-distribution job. The free
# Render plan has no Cron Jobs and no Shell, so there's no automatic
# scheduler wired up -- this button is the only way to actually run it in
# production until the plan is upgraded.
@admin_required
def run_subscription_distribution(request):
    if request.method == 'POST':
        call_command('distribute_subscription_revenue')
        messages.success(request, 'Subscription revenue distribution ran successfully.')
    return redirect('admin_subscription_revenue')


# Per-period breakdown: the pool, watch-time by course, each instructor's
# share, and the platform cut. This is the actual detail page -- the
# dashboard card only shows a count and a button to run the job.
@admin_required
def admin_subscription_revenue(request):
    periods = (
        SubscriptionPeriod.objects.select_related('subscription__student', 'subscription__plan')
        .prefetch_related('distributions__course', 'distributions__instructor')
        .order_by('-period_start')[:50]
    )
    due_count = SubscriptionPeriod.objects.filter(
        status=SubscriptionPeriod.Status.OPEN, period_end__lte=timezone.now()).count()
    return render(request, 'dashboard/admin_subscription_revenue.html', {
        'periods': periods,
        'due_subscription_periods_count': due_count,
    })


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
    # Parents first, each immediately followed by its own children, so the
    # nested taxonomy reads as a tree instead of an arbitrarily interleaved list.
    parents = Track.objects.filter(parent__isnull=True).order_by('order', 'name').prefetch_related(
        Prefetch('children', queryset=Track.objects.order_by('order', 'name'))
    )
    tracks = []
    for parent in parents:
        tracks.append(parent)
        tracks.extend(parent.children.all())

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
