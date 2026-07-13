from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Course, Lecture, InstructorWallet, Submission


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'is_student', 'is_instructor', 'is_staff')
    list_filter = ('is_student', 'is_instructor', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('LMS Info', {'fields': ('is_student', 'is_instructor', 'phone_number')}),
    )


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'instructor', 'category', 'level', 'price', 'is_published', 'created_at')
    list_filter = ('is_published', 'level', 'category')
    search_fields = ('title', 'instructor__username')


@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'order')
    list_filter = ('course',)


@admin.register(InstructorWallet)
class InstructorWalletAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'total_earnings', 'available_balance')


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('student', 'lecture', 'grade', 'submitted_at')
    list_filter = ('lecture__course',)
    search_fields = ('student__username',)
