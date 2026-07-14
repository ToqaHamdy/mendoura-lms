from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import (
    User, Course, InstructorWallet, Lecture, Module, Resource, Submission, Track, Category,
    Review, Payout,
)

INPUT_CLASSES = 'w-full px-4 py-3 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent focus:ring-2 focus:ring-brand-500 outline-none'


# 1. Student Registration Form
class StudentSignUpForm(UserCreationForm):
    phone_number = forms.CharField(
        max_length=15, required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Phone Number', 'class': INPUT_CLASSES})
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'phone_number')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', INPUT_CLASSES)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_student = True
        if commit:
            user.save()
        return user


# 2. Instructor Registration Form
class InstructorSignUpForm(UserCreationForm):
    phone_number = forms.CharField(
        max_length=15, required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Phone Number', 'class': INPUT_CLASSES})
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'phone_number')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', INPUT_CLASSES)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_instructor = True
        if commit:
            user.save()
            InstructorWallet.objects.get_or_create(instructor=user)
        return user


# 3. Course Creation Form
class CourseCreationForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ['title', 'description', 'track', 'category', 'level', 'production_type',
                  'price', 'is_free', 'thumbnail', 'ai_script']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. Introduction to Python',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'description': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'What is this course about?',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'track': forms.Select(attrs={
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'category': forms.Select(attrs={
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'level': forms.Select(attrs={
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'production_type': forms.RadioSelect(),
            'price': forms.NumberInput(attrs={
                'placeholder': '0.00',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'thumbnail': forms.ClearableFileInput(attrs={
                'class': 'w-full border border-gray-300 rounded-lg p-3'
            }),
            'ai_script': forms.Textarea(attrs={
                'rows': 6,
                'placeholder': 'Type the script here. Our AI will turn this text into a professional video lecture.',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['track'].queryset = Track.objects.filter(is_active=True)
        self.fields['category'].queryset = Category.objects.all()
        self.fields['category'].required = False


# Review Form (enrolled students only, enforced in the view)
class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.Select(choices=[(i, i) for i in range(1, 6)], attrs={
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'comment': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'Share your experience with this course...',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
        }


# 5. Submission Form (Student uploads homework)
class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ['submitted_file', 'submission_link', 'note']
        widgets = {
            'submitted_file': forms.ClearableFileInput(attrs={
                'class': 'w-full border border-gray-300 rounded-lg p-3'
            }),
            'submission_link': forms.URLInput(attrs={
                'placeholder': 'Optional: Google Drive / GitHub link',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'note': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Any notes for your instructor?',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
        }


# Track Form (Admin CRUD)
class TrackForm(forms.ModelForm):
    class Meta:
        model = Track
        fields = ['parent', 'name', 'description', 'icon', 'order']
        widgets = {
            'parent': forms.Select(attrs={'class': INPUT_CLASSES}),
            'name': forms.TextInput(attrs={'class': INPUT_CLASSES, 'placeholder': 'e.g. Web Development'}),
            'description': forms.Textarea(attrs={'class': INPUT_CLASSES, 'rows': 2}),
            'icon': forms.TextInput(attrs={'class': INPUT_CLASSES, 'placeholder': 'fa-laptop-code'}),
            'order': forms.NumberInput(attrs={'class': INPUT_CLASSES}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['parent'].queryset = Track.objects.filter(parent__isnull=True)
        self.fields['parent'].required = False


# Category Form (Admin CRUD)
class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['track', 'name']
        widgets = {
            'track': forms.Select(attrs={'class': INPUT_CLASSES}),
            'name': forms.TextInput(attrs={'class': INPUT_CLASSES, 'placeholder': 'e.g. Frontend'}),
        }


# Module Form (Instructor organizes course into sections)
class ModuleForm(forms.ModelForm):
    class Meta:
        model = Module
        fields = ['title', 'order']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. Getting Started',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
        }


# Resource Form (Instructor attaches downloadable files to a lecture)
class ResourceForm(forms.ModelForm):
    class Meta:
        model = Resource
        fields = ['title', 'file']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. Slides.pdf',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'file': forms.ClearableFileInput(attrs={
                'class': 'w-full border border-gray-300 dark:border-gray-700 rounded-lg p-3'
            }),
        }


# Payout Request Form (Instructor withdraws from available balance)
class PayoutRequestForm(forms.ModelForm):
    class Meta:
        model = Payout
        fields = ['amount', 'method']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'placeholder': '0.00',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'method': forms.TextInput(attrs={
                'placeholder': 'e.g. Bank transfer, PayPal',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
        }


# 4. Lecture Form (Instructor uploads video/materials)
class LectureForm(forms.ModelForm):
    class Meta:
        model = Lecture
        fields = ['title', 'video_url', 'video_file', 'is_preview', 'order']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. Introduction to Variables',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'video_url': forms.URLInput(attrs={
                'placeholder': 'https://youtube.com/... (optional if uploading a file)',
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
            'video_file': forms.ClearableFileInput(attrs={
                'class': 'w-full border border-gray-300 dark:border-gray-700 rounded-lg p-3'
            }),
            'order': forms.NumberInput(attrs={
                'class': 'w-full border border-gray-300 dark:border-gray-700 bg-transparent rounded-lg p-3 focus:ring-2 focus:ring-brand-500 outline-none'
            }),
        }
