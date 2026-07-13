from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CourseCreationForm, InstructorSignUpForm, LectureForm, ModuleForm, PayoutRequestForm,
    ResourceForm, ReviewForm, StudentSignUpForm,
)
from .models import (
    Certificate, Course, Enrollment, InstructorWallet, Lecture, LectureProgress, Module,
    Payment, Resource, Review, Track,
)

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


# Enroll in a course. Free courses enroll instantly; paid checkout is Phase 4.
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

    messages.info(request, 'Paid checkout is coming soon for this course.')
    return redirect('course_detail', course_id=course.id)


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


# Request a payout from available balance
@login_required
def request_payout(request):
    if not request.user.is_instructor:
        return redirect('platform_home')
    wallet, _ = InstructorWallet.objects.get_or_create(instructor=request.user)

    if request.method == 'POST':
        form = PayoutRequestForm(request.POST)
        if form.is_valid() and form.cleaned_data['amount'] <= wallet.available_balance:
            payout = form.save(commit=False)
            payout.wallet = wallet
            payout.save()
            messages.success(request, 'Payout request submitted.')
        else:
            messages.error(request, 'Payout amount cannot exceed your available balance.')

    return redirect('instructor_wallet')

# 9. Admin Dashboard - overview of all platform data
@login_required
def admin_dashboard(request):
    if not request.user.is_superuser:
        return redirect('platform_home')

    from .models import User, InstructorWallet
    context = {
        'users': User.objects.all().order_by('-date_joined'),
        'courses': Course.objects.all().order_by('-created_at'),
        'wallets': InstructorWallet.objects.select_related('instructor').all(),
        'total_students': User.objects.filter(is_student=True).count(),
        'total_instructors': User.objects.filter(is_instructor=True).count(),
        'total_courses': Course.objects.count(),
        'published_courses': Course.objects.filter(status=Course.Status.PUBLISHED).count(),
    }
    return render(request, 'dashboard/admin.html', context)
