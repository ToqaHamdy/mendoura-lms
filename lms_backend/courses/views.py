from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import CourseCreationForm, InstructorSignUpForm, ReviewForm, StudentSignUpForm
from .models import (
    Certificate, Course, Enrollment, Lecture, LectureProgress, Module, Review, Track,
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
    courses = Course.objects.filter(instructor=request.user)
    return render(request, 'dashboard/instructor.html', {'courses': courses})

# 5. Create Course View (The AI Script Submission)
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
            return redirect('instructor_dashboard')
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


# 10. Manage Lectures - List + Add lecture for a specific course
@login_required
def manage_lectures(request, course_id):
    from .forms import LectureForm
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    lectures = Lecture.objects.filter(module__course=course).select_related('module')

    if request.method == 'POST':
        form = LectureForm(request.POST, request.FILES)
        if form.is_valid():
            module, _ = Module.objects.get_or_create(
                course=course, title='General', defaults={'order': 0})
            lecture = form.save(commit=False)
            lecture.module = module
            lecture.save()
            return redirect('manage_lectures', course_id=course.id)
    else:
        form = LectureForm()

    return render(request, 'dashboard/manage_lectures.html', {
        'course': course,
        'lectures': lectures,
        'form': form,
    })
