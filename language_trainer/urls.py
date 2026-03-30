from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from trainer.forms import LoginForm
from trainer.views import RegisterView


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=LoginForm,
        ),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/register/", RegisterView.as_view(), name="register"),
    path("", include("trainer.urls")),
]
