import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.template.defaultfilters import slugify
from django.utils import timezone

from .money import calculate_split, get_instructor_share


class User(AbstractUser):
    is_student = models.BooleanField(default=False)
    is_instructor = models.BooleanField(default=False)
    phone_number = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return self.username


def _unique_slugify(instance, base_value, slug_field='slug'):
    """Generate a unique slug for instance's model, retrying with -2, -3, ... on collision."""
    slug = slugify(base_value)
    ModelClass = instance.__class__
    candidate = slug
    n = 2
    while ModelClass.objects.filter(**{slug_field: candidate}).exclude(pk=instance.pk).exists():
        candidate = f'{slug}-{n}'
        n += 1
    return candidate


class Track(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text='e.g. a Font Awesome class name')
    cover_image = models.ImageField(upload_to='track_covers/', blank=True, null=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slugify(self, self.name)
        super().save(*args, **kwargs)


class Category(models.Model):
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, blank=True)

    class Meta:
        ordering = ['name']
        unique_together = ('track', 'name')
        verbose_name_plural = 'Categories'

    def __str__(self):
        return f'{self.track.name} / {self.name}'

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Course(models.Model):
    class Level(models.TextChoices):
        BEGINNER = 'beginner', 'Beginner'
        INTERMEDIATE = 'intermediate', 'Intermediate'
        ADVANCED = 'advanced', 'Advanced'

    class ProductionType(models.TextChoices):
        FULL = 'full', 'Full production by instructor'
        SCRIPT_ONLY = 'script_only', 'Script only (platform produces)'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING_REVIEW = 'pending_review', 'Pending Review'
        PUBLISHED = 'published', 'Published'
        REJECTED = 'rejected', 'Rejected'

    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='courses',
        limit_choices_to={'is_instructor': True}
    )
    # null=True: no Track exists to default to; required at the form layer instead.
    track = models.ForeignKey(Track, on_delete=models.PROTECT, related_name='courses', null=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, related_name='courses',
                                  null=True, blank=True)

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True, default='')
    subtitle = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField()
    what_you_will_learn = models.TextField(blank=True, default='')
    requirements = models.TextField(blank=True, default='')
    language = models.CharField(max_length=50, default='English')
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.BEGINNER)
    duration_hours = models.PositiveIntegerField(default=0)

    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    is_free = models.BooleanField(default=False)

    # null=True: never silently default a field that drives revenue split.
    production_type = models.CharField(max_length=20, choices=ProductionType.choices, null=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    rejection_reason = models.TextField(blank=True, default='')

    thumbnail = models.ImageField(upload_to='course_thumbnails/', blank=True, null=True)
    ai_script = models.TextField(help_text='The script for AI video generation', blank=True, null=True)

    rating = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal('0.00'))
    students_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    def has_successful_sale(self):
        return self.payments.filter(status=Payment.Status.SUCCEEDED).exists()

    def get_instructor_share_percentage(self) -> Decimal:
        return get_instructor_share(self.production_type)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slugify(self, self.title)
        if self.pk and self.production_type:
            previous = Course.objects.only('production_type').get(pk=self.pk)
            if previous.production_type != self.production_type and self.has_successful_sale():
                raise ValidationError(
                    "production_type is read-only once a course has its first successful sale."
                )
        super().save(*args, **kwargs)


class Module(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='modules')
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'{self.course.title} - {self.title}'


class Lecture(models.Model):
    class ContentType(models.TextChoices):
        VIDEO = 'video', 'Video'
        ARTICLE = 'article', 'Article'
        QUIZ = 'quiz', 'Quiz'

    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='lectures', null=True)
    title = models.CharField(max_length=255)
    content_type = models.CharField(max_length=20, choices=ContentType.choices, default=ContentType.VIDEO)
    video_url = models.URLField(blank=True, null=True)
    video_file = models.FileField(upload_to='lecture_videos/', blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    is_preview = models.BooleanField(default=False, help_text='Viewable for free without enrollment')
    order = models.PositiveIntegerField(default=0)
    ai_generated_script = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'{self.module.course.title} - {self.title}'

    @property
    def course(self):
        return self.module.course


class Resource(models.Model):
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='resources')
    file = models.FileField(upload_to='lecture_resources/',
                             help_text='Any file: PDF, zip, image, audio, slides, etc.')
    title = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0, help_text='Size in bytes')

    def __str__(self):
        return self.title or self.file.name


class Submission(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='submissions')
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='submissions')
    submitted_file = models.FileField(upload_to='submissions/', blank=True, null=True)
    submission_link = models.URLField(blank=True, null=True, help_text='Link to Google Drive, GitHub, etc.')
    note = models.TextField(blank=True, null=True)
    grade = models.CharField(max_length=50, blank=True, null=True)
    feedback = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.student.username} - {self.lecture.title}"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name='payments')
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='payments')

    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # ═══ Frozen at creation. Immutable forever. ═══
    production_type_at_purchase = models.CharField(max_length=20, editable=False)
    instructor_share_percentage = models.DecimalField(max_digits=5, decimal_places=2, editable=False)
    instructor_amount = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    platform_amount = models.DecimalField(max_digits=10, decimal_places=2, editable=False)

    provider_transaction_id = models.CharField(max_length=255, unique=True, null=True, blank=True,
                                                db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # The DB itself rejects a bad split, even if the code is wrong.
            models.CheckConstraint(
                condition=models.Q(total_amount=models.F('instructor_amount') + models.F('platform_amount')),
                name='payment_split_sums_to_total',
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name='payment_amount_non_negative',
            ),
        ]

    def __str__(self):
        return f'{self.student} -> {self.course} (${self.total_amount})'

    def save(self, *args, **kwargs):
        if self._state.adding:
            # Percentage is READ FROM THE COURSE -- never passed in by hand.
            self.production_type_at_purchase = self.course.production_type
            self.instructor_share_percentage = get_instructor_share(self.production_type_at_purchase)
            self.instructor_amount, self.platform_amount = calculate_split(
                self.total_amount, self.instructor_share_percentage)
        else:
            # Frozen fields cannot change after creation.
            frozen = Payment.objects.only(
                'total_amount', 'instructor_amount', 'platform_amount',
                'instructor_share_percentage').get(pk=self.pk)
            for f in ('total_amount', 'instructor_amount',
                      'platform_amount', 'instructor_share_percentage'):
                if getattr(self, f) != getattr(frozen, f):
                    raise ValidationError(f"'{f}' is immutable after creation.")
        super().save(*args, **kwargs)


class Enrollment(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='enrollments')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='enrollments')
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='enrollment')
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'course')
        ordering = ['-enrolled_at']

    def __str__(self):
        return f'{self.student} enrolled in {self.course}'

    def total_lecture_count(self):
        return Lecture.objects.filter(module__course=self.course).count()

    def completed_lecture_count(self):
        return self.lecture_progress.filter(completed=True).count()

    def progress_percent(self):
        total = self.total_lecture_count()
        if not total:
            return 0
        return round(self.completed_lecture_count() * 100 / total)

    def is_complete(self):
        total = self.total_lecture_count()
        return total > 0 and self.completed_lecture_count() >= total

    def issue_certificate_if_complete(self):
        if self.is_complete():
            certificate, _ = Certificate.objects.get_or_create(enrollment=self)
            return certificate
        return None


class LectureProgress(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name='lecture_progress')
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='progress_entries')
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_position_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('enrollment', 'lecture')

    def __str__(self):
        return f'{self.enrollment.student} - {self.lecture} ({"done" if self.completed else "in progress"})'


class InstructorWallet(models.Model):
    instructor = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        limit_choices_to={'is_instructor': True}
    )
    total_earnings = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    available_balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    pending_balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_withdrawn = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return f"{self.instructor.username}'s Wallet"


class WalletTransaction(models.Model):
    class Type(models.TextChoices):
        SALE_CREDIT = 'sale_credit', 'Sale Credit'
        WITHDRAWAL = 'withdrawal', 'Withdrawal'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        REFUND_DEBIT = 'refund_debit', 'Refund Debit'

    wallet = models.ForeignKey(InstructorWallet, on_delete=models.CASCADE, related_name='transactions')
    type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='wallet_transactions')
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.wallet.instructor.username}: {self.get_type_display()} ${self.amount}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('WalletTransaction rows are append-only and cannot be modified.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('WalletTransaction rows are append-only and cannot be deleted.')


class Payout(models.Model):
    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        APPROVED = 'approved', 'Approved'
        PAID = 'paid', 'Paid'
        REJECTED = 'rejected', 'Rejected'

    wallet = models.ForeignKey(InstructorWallet, on_delete=models.CASCADE, related_name='payouts')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    method = models.CharField(max_length=100, blank=True)
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.wallet.instructor.username} payout ${self.amount} ({self.status})'


class Review(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'course')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.student.username} rated {self.course.title}: {self.rating}/5'


class Certificate(models.Model):
    enrollment = models.OneToOneField(Enrollment, on_delete=models.CASCADE, related_name='certificate')
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    issued_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Certificate for {self.enrollment}'
