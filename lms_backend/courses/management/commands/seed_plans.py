from django.core.management.base import BaseCommand

from courses.models import Plan


class Command(BaseCommand):
    help = 'Seed the Mendoura Annual Pass subscription plan (idempotent, safe to re-run).'

    def handle(self, *args, **options):
        plan, created = Plan.objects.update_or_create(
            name='Mendoura Annual Pass',
            defaults={
                'price_egp': 1499,
                'price_usd': 49,
                'duration_days': 365,
                'is_active': True,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f'Created plan: {plan.name}') if created
            else f'Already exists: {plan.name}'
        )
