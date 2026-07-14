from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    User, Track, TrackRoadmapStep, Category, Course, Module, Lecture, Resource, Submission,
    Payment, Enrollment, LectureProgress, InstructorWallet, WalletTransaction,
    Payout, Review, Certificate,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'is_student', 'is_instructor', 'is_staff')
    list_filter = ('is_student', 'is_instructor', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('LMS Info', {'fields': ('is_student', 'is_instructor', 'phone_number')}),
    )


class TrackRoadmapStepInline(admin.TabularInline):
    model = TrackRoadmapStep
    extra = 1
    fields = ('order', 'title', 'course', 'is_optional')


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'order', 'is_active')
    list_filter = ('is_active', 'parent')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [TrackRoadmapStepInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'track')
    list_filter = ('track',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'instructor', 'track', 'category', 'level', 'production_type',
                     'price', 'status', 'created_at')
    list_filter = ('status', 'production_type', 'level', 'track')
    search_fields = ('title', 'instructor__username')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'order')
    list_filter = ('course',)


@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = ('title', 'module', 'content_type', 'is_preview', 'order')
    list_filter = ('content_type', 'is_preview')


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ('title', 'lecture', 'file_size')


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('student', 'lecture', 'grade', 'submitted_at')
    list_filter = ('lecture__module__course',)
    search_fields = ('student__username',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'total_amount', 'instructor_amount',
                     'platform_amount', 'status', 'created_at')
    list_filter = ('status', 'production_type_at_purchase')
    search_fields = ('student__username', 'course__title', 'provider_transaction_id')


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'enrolled_at')
    list_filter = ('course',)
    search_fields = ('student__username', 'course__title')


@admin.register(LectureProgress)
class LectureProgressAdmin(admin.ModelAdmin):
    list_display = ('enrollment', 'lecture', 'completed', 'completed_at')
    list_filter = ('completed',)


@admin.register(InstructorWallet)
class InstructorWalletAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'total_earnings', 'available_balance',
                     'pending_balance', 'total_withdrawn')


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'type', 'amount', 'balance_after', 'created_at')
    list_filter = ('type',)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'amount', 'status', 'requested_at', 'processed_at')
    list_filter = ('status',)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'rating', 'created_at')
    list_filter = ('rating',)


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ('enrollment', 'uuid', 'issued_at')
