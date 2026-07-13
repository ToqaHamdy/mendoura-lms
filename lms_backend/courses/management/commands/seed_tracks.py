from django.core.management.base import BaseCommand

from courses.models import Track

TRACK_NAMES = [
    'Web Development',
    'Mobile Development',
    'Data Science & AI',
    'Cybersecurity',
    'Cloud & DevOps',
    'UI/UX Design',
    'Game Development',
    'English',
    'French',
    'German',
    'Spanish',
    'Business & Marketing',
]


class Command(BaseCommand):
    help = 'Seed the initial set of Tracks (idempotent, safe to re-run).'

    def handle(self, *args, **options):
        for order, name in enumerate(TRACK_NAMES):
            track, created = Track.objects.get_or_create(name=name, defaults={'order': order})
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created track: {name}'))
            else:
                self.stdout.write(f'Already exists: {name}')
