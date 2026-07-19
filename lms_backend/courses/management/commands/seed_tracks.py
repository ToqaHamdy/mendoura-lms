from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import Track, TrackRoadmapStep

# Two-level taxonomy: each top-level entry is a parent Track (e.g. "Tech"),
# each child is a leaf Track courses actually attach to (e.g. "Cybersecurity").
# "roadmap" is the ordered set of planned course steps for that leaf track --
# TrackRoadmapStep.course stays null until a real course is published and an
# admin links it.
#
# "name_ar" is the hand-curated Arabic translation (see the modeltranslation
# rollout this shipped alongside) -- these are the most visible strings on
# the site (navbar, homepage cards), so they're reviewed real Arabic, not
# machine-translated.
TAXONOMY = [
    {
        'name': 'Tech',
        'name_ar': 'تكنولوجيا',
        'icon': 'fa-microchip',
        'children': [
            {
                'name': 'Artificial Intelligence & Machine Learning',
                'name_ar': 'الذكاء الاصطناعي وتعلم الآلة',
                'icon': 'fa-brain',
                'roadmap': [
                    'Python for AI',
                    'Mathematics for Machine Learning',
                    'Machine Learning Foundations',
                    'Deep Learning with Neural Networks',
                    'Natural Language Processing',
                    'AI Capstone Project',
                ],
            },
            {
                'name': 'Cybersecurity',
                'name_ar': 'الأمن السيبراني',
                'icon': 'fa-shield-halved',
                'roadmap': [
                    'Networking Fundamentals',
                    'Security Foundations',
                    'Ethical Hacking Basics',
                    'Penetration Testing',
                    'Security Operations (SOC)',
                    'Cybersecurity Capstone',
                ],
            },
            {
                'name': 'Web Development',
                'name_ar': 'تطوير الويب',
                'icon': 'fa-laptop-code',
                'roadmap': [
                    'HTML, CSS & JavaScript Foundations',
                    'Responsive Web Design',
                    'Front-End Frameworks (React)',
                    'Back-End Development with Django',
                    'Databases for Web Apps',
                    'Full-Stack Capstone Project',
                ],
            },
            {
                'name': 'Mobile Development',
                'name_ar': 'تطوير تطبيقات الهاتف',
                'icon': 'fa-mobile-screen-button',
                'roadmap': [
                    'Programming Foundations',
                    'Android Development with Kotlin',
                    'iOS Development with Swift',
                    'Cross-Platform Apps with Flutter',
                    'Mobile App Capstone',
                ],
            },
            {
                'name': 'Data Science',
                'name_ar': 'علم البيانات',
                'icon': 'fa-chart-line',
                'roadmap': [
                    'Python for Data Analysis',
                    'Statistics & Probability',
                    'Data Wrangling with Pandas',
                    'Data Visualization',
                    'Machine Learning for Data Science',
                    'Data Science Capstone',
                ],
            },
            {
                'name': 'Cloud & DevOps',
                'name_ar': 'الحوسبة السحابية و DevOps',
                'icon': 'fa-cloud',
                'roadmap': [
                    'Linux & Command Line Basics',
                    'Cloud Fundamentals (AWS/Azure/GCP)',
                    'Containers with Docker',
                    'Orchestration with Kubernetes',
                    'CI/CD Pipelines',
                    'DevOps Capstone',
                ],
            },
            {
                'name': 'Game Development',
                'name_ar': 'تطوير الألعاب',
                'icon': 'fa-gamepad',
                'roadmap': [
                    'Programming Foundations',
                    'Game Design Principles',
                    '2D Game Development with Unity',
                    '3D Game Development',
                    'Game Physics & AI',
                    'Game Dev Capstone',
                ],
            },
        ],
    },
    {
        'name': 'Languages',
        'name_ar': 'اللغات',
        'icon': 'fa-language',
        'children': [
            {
                'name': 'English',
                'name_ar': 'اللغة الإنجليزية',
                'icon': 'fa-comment',
                'roadmap': ['Beginner English', 'Intermediate English', 'Business English', 'Advanced Fluency & Writing'],
            },
            {
                'name': 'French',
                'name_ar': 'اللغة الفرنسية',
                'icon': 'fa-comment',
                'roadmap': ['Beginner French', 'Intermediate French', 'Conversational French', 'Advanced French'],
            },
            {
                'name': 'German',
                'name_ar': 'اللغة الألمانية',
                'icon': 'fa-comment',
                'roadmap': ['Beginner German', 'Intermediate German', 'Conversational German', 'Advanced German'],
            },
            {
                'name': 'Spanish',
                'name_ar': 'اللغة الإسبانية',
                'icon': 'fa-comment',
                'roadmap': ['Beginner Spanish', 'Intermediate Spanish', 'Conversational Spanish', 'Advanced Spanish'],
            },
            {
                'name': 'Italian',
                'name_ar': 'اللغة الإيطالية',
                'icon': 'fa-comment',
                'roadmap': ['Beginner Italian', 'Intermediate Italian', 'Conversational Italian'],
            },
            {
                'name': 'Turkish',
                'name_ar': 'اللغة التركية',
                'icon': 'fa-comment',
                'roadmap': ['Beginner Turkish', 'Intermediate Turkish', 'Conversational Turkish'],
            },
        ],
    },
    {
        'name': 'Marketing',
        'name_ar': 'تسويق',
        'icon': 'fa-bullhorn',
        'children': [
            {
                'name': 'Digital Marketing',
                'name_ar': 'التسويق الرقمي',
                'icon': 'fa-chart-simple',
                'roadmap': [
                    'Marketing Fundamentals',
                    'Digital Marketing Strategy',
                    'Google Ads & SEM',
                    'Analytics & Growth Tracking',
                    'Digital Marketing Capstone',
                ],
            },
            {
                'name': 'SEO',
                'name_ar': 'تحسين محركات البحث',
                'icon': 'fa-magnifying-glass-chart',
                'roadmap': ['SEO Fundamentals', 'Keyword Research', 'On-Page & Technical SEO', 'Link Building & Off-Page SEO'],
            },
            {
                'name': 'Content',
                'name_ar': 'المحتوى',
                'icon': 'fa-pen-nib',
                'roadmap': ['Content Strategy Fundamentals', 'Copywriting Essentials', 'Content Creation for Social & Blog', 'Content Analytics'],
            },
            {
                'name': 'Social Media',
                'name_ar': 'التواصل الاجتماعي',
                'icon': 'fa-share-nodes',
                'roadmap': [
                    'Social Media Fundamentals',
                    'Platform Strategy (Instagram/TikTok/LinkedIn)',
                    'Paid Social Advertising',
                    'Community Management & Analytics',
                ],
            },
        ],
    },
    {
        'name': 'Business',
        'name_ar': 'إدارة أعمال',
        'icon': 'fa-briefcase',
        'children': [
            {
                'name': 'Entrepreneurship',
                'name_ar': 'ريادة الأعمال',
                'icon': 'fa-rocket',
                'roadmap': ['Foundations of Entrepreneurship', 'Business Model Design', 'Startup Fundraising', 'Growth & Scaling a Business'],
            },
            {
                'name': 'Finance',
                'name_ar': 'المالية',
                'icon': 'fa-sack-dollar',
                'roadmap': ['Financial Literacy Basics', 'Corporate Finance Fundamentals', 'Financial Modeling & Analysis', 'Investment & Portfolio Management'],
            },
            {
                'name': 'Project Management',
                'name_ar': 'إدارة المشاريع',
                'icon': 'fa-diagram-project',
                'roadmap': ['Project Management Foundations', 'Agile & Scrum', 'Project Planning & Risk Management', 'PM Capstone / Certification Prep'],
            },
        ],
    },
    {
        'name': 'Design',
        'name_ar': 'تصميم',
        'icon': 'fa-palette',
        'children': [
            {
                'name': 'UI/UX Design',
                'name_ar': 'تصميم واجهات وتجربة المستخدم',
                'icon': 'fa-pen-ruler',
                'roadmap': [
                    'Design Thinking Foundations',
                    'User Research Fundamentals',
                    'Wireframing & Prototyping (Figma)',
                    'Visual & Interaction Design',
                    'UX Capstone Project',
                ],
            },
            {
                'name': 'Graphic Design',
                'name_ar': 'التصميم الجرافيكي',
                'icon': 'fa-swatchbook',
                'roadmap': ['Design Principles Foundations', 'Typography & Color Theory', 'Adobe Illustrator Essentials', 'Adobe Photoshop Essentials', 'Branding & Identity Design'],
            },
            {
                'name': 'Motion Design',
                'name_ar': 'تصميم الموشن جرافيك',
                'icon': 'fa-film',
                'roadmap': ['Animation Principles', 'After Effects Foundations', 'Motion Graphics for Video', '3D Motion Basics'],
            },
        ],
    },
]


class Command(BaseCommand):
    help = (
        'Sync the Tracks taxonomy (parents + nested children + roadmap steps) '
        'from the TAXONOMY constant below. Idempotent and safe to run on every '
        "deploy: existing tracks are updated in place by name (never deleted "
        "while still referenced -- Course.track is on_delete=PROTECT), and only "
        "tracks that are no longer part of the taxonomy at all get removed."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        current_names = {t['name'] for t in TAXONOMY}
        current_names |= {c['name'] for t in TAXONOMY for c in t['children']}

        obsolete = Track.objects.exclude(name__in=current_names)
        obsolete_names = list(obsolete.values_list('name', flat=True))
        if obsolete_names:
            obsolete.delete()
            self.stdout.write(f"Removed obsolete tracks: {', '.join(obsolete_names)}")

        for parent_order, parent_data in enumerate(TAXONOMY):
            parent, created = Track.objects.update_or_create(
                name=parent_data['name'],
                defaults={
                    'parent': None, 'icon': parent_data['icon'], 'order': parent_order,
                    'name_ar': parent_data['name_ar'],
                },
            )
            self.stdout.write(
                self.style.SUCCESS(f'Created parent track: {parent.name}') if created
                else f'Already exists: {parent.name}'
            )

            for child_order, child_data in enumerate(parent_data['children']):
                child, created = Track.objects.update_or_create(
                    name=child_data['name'],
                    defaults={
                        'parent': parent, 'icon': child_data['icon'], 'order': child_order,
                        'name_ar': child_data['name_ar'],
                    },
                )
                self.stdout.write(f'  {"Created" if created else "Already exists"}: {child.name}')

                roadmap = child_data['roadmap']
                for step_order, step_title in enumerate(roadmap):
                    TrackRoadmapStep.objects.update_or_create(
                        track=child, order=step_order,
                        defaults={'title': step_title},
                    )
                # Drop any steps left over from a previously longer roadmap.
                TrackRoadmapStep.objects.filter(track=child, order__gte=len(roadmap)).delete()
                self.stdout.write(f'    Synced {len(roadmap)} roadmap steps')
