from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User, Course

# 1. Student Registration Form
class StudentSignUpForm(UserCreationForm):
    phone_number = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={'placeholder': 'Phone Number'}))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'phone_number')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_student = True
        if commit:
            user.save()
        return user

# 2. Instructor Registration Form
class InstructorSignUpForm(UserCreationForm):
    phone_number = forms.CharField(max_length=15, required=True, widget=forms.TextInput(attrs={'placeholder': 'Phone Number'}))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'phone_number')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_instructor = True
        if commit:
            user.save()
        return user

# 3. Course Creation Form (updated with category, level, price, thumbnail)
class CourseCreationForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ['title', 'description', 'category', 'level', 'price', 'thumbnail', 'ai_script', 'is_published']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. Introduction to Python',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'description': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'What is this course about?',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'category': forms.TextInput(attrs={
                'placeholder': 'e.g. Web Development',
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
            'level': forms.Select(attrs={
                'class': 'w-full border border-gray-300 rounded-lg p-3 focus:ring-2 focus:ring-indigo-500 outline-none'
            }),
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
