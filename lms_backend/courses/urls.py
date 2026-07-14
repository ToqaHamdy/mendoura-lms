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
    path('course/<int:course_id>/toggle-publish/', views.toggle_publish, name='toggle_publish'),
    path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),

    # Admin interface
    path('dashboard/admin/courses/', views.course_approval_queue, name='course_approval_queue'),
    path('dashboard/admin/courses/<int:course_id>/approve/', views.approve_course, name='approve_course'),
    path('dashboard/admin/courses/<int:course_id>/reject/', views.reject_course, name='reject_course'),
    path('dashboard/admin/users/', views.admin_users, name='admin_users'),
    path('dashboard/admin/payments/', views.admin_payments, name='admin_payments'),
    path('dashboard/admin/payouts/', views.admin_payouts, name='admin_payouts'),
    path('dashboard/admin/payouts/<int:payout_id>/approve/', views.approve_payout, name='approve_payout'),
    path('dashboard/admin/payouts/<int:payout_id>/reject/', views.reject_payout, name='reject_payout'),
    path('dashboard/admin/payouts/<int:payout_id>/paid/', views.mark_payout_paid, name='mark_payout_paid'),
    path('dashboard/admin/tracks/', views.admin_tracks, name='admin_tracks'),
    path('dashboard/admin/tracks/<int:track_id>/toggle/', views.toggle_track_active, name='toggle_track_active'),
    path('dashboard/admin/categories/', views.admin_categories, name='admin_categories'),
    path('dashboard/admin/categories/<int:category_id>/delete/', views.delete_category, name='delete_category'),

    # Instructor interface
    path('course/<int:course_id>/modules/', views.manage_modules, name='manage_modules'),
    path('course/<int:course_id>/modules/<int:module_id>/lectures/', views.manage_lectures,
         name='manage_lectures'),
    path('lectures/<int:lecture_id>/resources/', views.add_resource, name='add_resource'),
    path('course/<int:course_id>/students/', views.course_students, name='course_students'),
    path('dashboard/wallet/', views.instructor_wallet, name='instructor_wallet'),
    path('dashboard/wallet/payout/', views.request_payout, name='request_payout'),

    # Student interface
    path('tracks/', views.track_list, name='track_list'),
    path('tracks/<slug:slug>/', views.track_detail, name='track_detail'),
    path('search/', views.search_results, name='search_results'),
    path('courses/<int:course_id>/enroll/', views.enroll_course, name='enroll_course'),
    path('courses/<int:course_id>/checkout/', views.checkout_course, name='checkout_course'),
    path('courses/<int:course_id>/review/', views.add_review, name='add_review'),
    path('learning/', views.my_learning, name='my_learning'),
    path('learn/<int:course_id>/<int:lecture_id>/', views.course_player, name='course_player'),
    path('learn/<int:course_id>/<int:lecture_id>/complete/', views.mark_lecture_complete,
         name='mark_lecture_complete'),
    path('certificates/<uuid:certificate_uuid>/', views.certificate_view, name='certificate_view'),
]