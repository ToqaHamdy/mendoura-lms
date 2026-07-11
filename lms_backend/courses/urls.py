from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.platform_home, name='platform_home'),
    path('signup/student/', views.student_signup, name='student_signup'),
    path('signup/instructor/', views.instructor_signup, name='instructor_signup'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/instructor/', views.instructor_dashboard, name='instructor_dashboard'),
    path('course/create/', views.create_course, name='create_course'),
    path('courses/', views.course_catalog, name='course_catalog'),
    path('courses/<int:course_id>/', views.course_detail, name='course_detail'),
    path('courses/', views.course_catalog, name='course_catalog'),
    path('courses/<int:course_id>/', views.course_detail, name='course_detail'),
    path('course/<int:course_id>/toggle-publish/', views.toggle_publish, name='toggle_publish'),
    path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),
]