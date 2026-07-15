from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from courses import views as courses_views


# TEMPORARY -- remove when off the free Render tier. No DB hit, just proves
# the process is alive, so an external cron (cron-job.org / UptimeRobot)
# pinging this every 10-14 min keeps the free instance from sleeping.
def healthz(request):
    return HttpResponse('ok')


urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz/', healthz, name='healthz'),
    path('webhooks/paymob/', courses_views.paymob_webhook, name='paymob_webhook'),
    path('webhooks/bunny/', courses_views.bunny_webhook, name='bunny_webhook'),
    path('i18n/', include('django.conf.urls.i18n')),  # provides the 'set_language' POST endpoint
    path('', include('courses.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
