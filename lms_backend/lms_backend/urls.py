from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from courses import views as courses_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('webhooks/paymob/', courses_views.paymob_webhook, name='paymob_webhook'),
    path('', include('courses.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
