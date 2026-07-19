"""django-modeltranslation registrations -- DATABASE content translation
(track names, course titles, module/lecture titles, plan names/features),
distinct from the UI string translation `{% trans %}` already handles.

Scope is deliberate (see the PR this shipped in): Track and Plan are fully
translated (curated by admins/seed data) since there are few of them and
they drive top-level navigation. Course/Module/Lecture are registered here
too -- so instructors *can* optionally fill in a translation of their own
course via the course form -- but nothing auto-translates their content;
an untranslated field just falls back to the original per
MODELTRANSLATION_FALLBACK_LANGUAGES.
"""
from modeltranslation.translator import TranslationOptions, register

from .models import Course, Lecture, Module, Plan, Track


@register(Track)
class TrackTranslationOptions(TranslationOptions):
    fields = ('name', 'description')


@register(Course)
class CourseTranslationOptions(TranslationOptions):
    fields = ('title', 'subtitle', 'description', 'what_you_will_learn', 'requirements')


@register(Module)
class ModuleTranslationOptions(TranslationOptions):
    fields = ('title',)


@register(Lecture)
class LectureTranslationOptions(TranslationOptions):
    fields = ('title',)


@register(Plan)
class PlanTranslationOptions(TranslationOptions):
    fields = ('name', 'features')
