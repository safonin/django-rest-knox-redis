"""
URL configuration for tests.
"""

from django.urls import path

from knox_redis.views import LoginView, LogoutAllView, LogoutView

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="knox_login"),
    path("auth/logout/", LogoutView.as_view(), name="knox_logout"),
    path("auth/logoutall/", LogoutAllView.as_view(), name="knox_logoutall"),
]
