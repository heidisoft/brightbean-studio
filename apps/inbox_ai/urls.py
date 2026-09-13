from django.urls import path

from . import views

app_name = "inbox_ai"
urlpatterns = [path("<uuid:message_id>/generate/", views.generate, name="generate")]
