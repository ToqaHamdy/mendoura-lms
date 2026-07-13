from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from .forms import StudentSignUpForm, InstructorSignUpForm, CourseCreationForm
from .models import Course

# 1. Platform Homepage
def platform_home(request):
    return render(request, 'platform_home.html')

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
    courses = Course.objects.filter(is_published=True).order_by('-created_at')
    return render(request, 'courses/catalog.html', {'courses': courses})

# 7. Course Detail - View a single course + its lectures
def course_detail(request, course_id):
    course = get_object_or_404(Course, id=course_id, is_published=True)
    lectures = course.lectures.all()
    return render(request, 'courses/detail.html', {'course': course, 'lectures': lectures})

# 8. Toggle Publish Status
@login_required
def toggle_publish(request, course_id):
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    course.is_published = not course.is_published
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
        'published_courses': Course.objects.filter(is_published=True).count(),
    }
    return render(request, 'dashboard/admin.html', context)


# 10. Manage Lectures - List + Add lecture for a specific course
@login_required
def manage_lectures(request, course_id):
    from .forms import LectureForm
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    lectures = course.lectures.all()

    if request.method == 'POST':
        form = LectureForm(request.POST, request.FILES)
        if form.is_valid():
            lecture = form.save(commit=False)
            lecture.course = course
            lecture.save()
            return redirect('manage_lectures', course_id=course.id)
    else:
        form = LectureForm()

    return render(request, 'dashboard/manage_lectures.html', {
        'course': course,
        'lectures': lectures,
        'form': form,
    })
