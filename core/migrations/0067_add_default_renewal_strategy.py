# -*- coding: utf-8 -*-
# Generated by Django 1.9.8 on 2016-12-14 20:33
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_auto_20161026_1659'),
    ]

    operations = [
        migrations.AlterField(
            model_name='allocationsource',
            name='renewal_strategy',
            field=models.CharField(default=b'default', max_length=255),
        ),
    ]
