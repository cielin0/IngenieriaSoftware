import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0002_alter_user_managers'),
    ]

    operations = [
        migrations.CreateModel(
            name='Community',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=60, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('lat', models.FloatField(help_text='Latitud del centro de la comunidad.')),
                ('lng', models.FloatField(help_text='Longitud del centro de la comunidad.')),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name_plural': 'communities',
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='user',
            name='community',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='members', to='incidents.community'),
        ),
        migrations.AddField(
            model_name='user',
            name='reputation',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name='incident',
            options={'ordering': ['-time', 'id']},
        ),
        migrations.AddField(
            model_name='incident',
            name='community',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='incidents', to='incidents.community'),
        ),
        migrations.AddField(
            model_name='incident',
            name='expires_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='incident',
            name='veracity',
            field=models.CharField(choices=[('sin_verificar', 'Sin verificar'), ('confirmado_comunidad', 'Confirmado por la comunidad'), ('verificado', 'Verificado por un operador'), ('probablemente_falso', 'Probablemente falso'), ('falso', 'Falso (descartado)')], db_index=True, default='sin_verificar', max_length=24),
        ),
        migrations.AddField(
            model_name='incident',
            name='veracity_score',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='incident',
            name='confirm_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='incident',
            name='deny_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='incident',
            name='hidden',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='incident',
            name='hidden_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='incident',
            name='flag_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='incident',
            name='flag_score',
            field=models.FloatField(default=0.0),
        ),
        migrations.CreateModel(
            name='Vote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.SmallIntegerField()),
                ('weight', models.FloatField(default=1.0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('incident', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='votes', to='incidents.incident')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='votes', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='vote',
            constraint=models.UniqueConstraint(fields=('incident', 'user'), name='unique_vote_per_incident_user'),
        ),
        migrations.CreateModel(
            name='Flag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(max_length=20)),
                ('weight', models.FloatField(default=1.0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('incident', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='flags', to='incidents.incident')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='flags', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='flag',
            constraint=models.UniqueConstraint(fields=('incident', 'user'), name='unique_flag_per_incident_user'),
        ),
        migrations.CreateModel(
            name='ReputationEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('delta', models.IntegerField()),
                ('reason', models.CharField(max_length=120)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('incident', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reputation_events', to='incidents.incident')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reputation_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PasswordResetToken',
            fields=[
                ('digest', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reset_tokens', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
