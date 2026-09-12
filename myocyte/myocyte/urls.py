"""URL configuration for myocyte project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/

Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))

"""

from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import (
    PasswordResetConfirmView,
    PasswordResetCompleteView,
    PasswordResetDoneView,
)
from django.urls import path
from toxtempass import views

from myocyte import settings

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "init/<slug:label>",
        views.init_db,
        name="init_db",
        # e.g., /init/v1 -> loads ToxTemp_v1.json questions into the database
    ),  # initializes database, meaning it creates the questions, sub/sections.
]

urlpatterns += [
    path("", views.AssayListView.as_view(), name="overview"),
    path("add/", views.new_form_view, name="add_new"),
    # Login stuff
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("login/orcid/", views.orcid_login, name="orcid_login"),
    path("login/signup/", views.signup, name="signup"),
    path(
        "login/signup/ror-organizations/",
        views.ror_organization_lookup,
        name="ror_organization_lookup",
    ),
    path("orcid/callback/", views.orcid_callback, name="orcid_callback"),
    path("orcid/signup/", views.orcid_signup, name="orcid_signup"),
    # Password reset
    path(
        "password-reset/",
        views.PasswordResetRequestView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        PasswordResetDoneView.as_view(template_name="toxtempass/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "password-reset/confirm/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(
            template_name="toxtempass/password_reset_confirm.html",
            success_url="/password-reset/complete/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        PasswordResetCompleteView.as_view(template_name="toxtempass/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    # Beta flows
    path("beta/approve/<str:token>/", views.approve_beta, name="approve_beta"),
    path("beta/wait/", views.beta_wait, name="beta_wait"),
    # Admin beta management
    path(
        "beta/users/", views.AdminBetaUserListView.as_view(), name="admin_beta_user_list"
    ),
    path("beta/toggle-beta/", views.toggle_beta_admitted, name="toggle_beta_admitted"),
    # Staff KPI dashboard (aggregates only — see toxtempass/stats.py)
    path("stats/", views.stats_dashboard, name="stats_dashboard"),
    path("stats/data.json", views.stats_data, name="stats_data"),
    path("stats/export.csv", views.stats_export_csv, name="stats_export_csv"),
    # User preferences
    path(
        "settings/llm-model/",
        views.set_llm_preference,
        name="set_llm_preference",
    ),
    # Investigation URLs
    path(
        "investigation/create/",
        views.create_or_update_investigation,
        name="create_investigation",
    ),
    path(
        "investigation/update/<int:pk>/",  # hard-coded in new.html
        views.create_or_update_investigation,
        name="update_investigation",
    ),
    path(
        "investigation/delete/<int:pk>/",  # hard-coded in new.html
        views.delete_investigation,
        name="delete_investigation",
    ),
    # Study URLs
    path("study/create/", views.create_or_update_study, name="create_study"),
    path(
        "study/update/<int:pk>/", views.create_or_update_study, name="update_study"
    ),  # hard-coded in new.html
    path(
        "study/delete/<int:pk>/", views.delete_study, name="delete_study"
    ),  # hard-coded in new.html
    # Assay URLs
    path("assay/create/", views.create_or_update_assay, name="create_assay"),
    path(
        "assay/gpt-allowed/<int:pk>",
        views.initial_gpt_allowed_for_assay,
        name="assay_gpt_allowed",
    ),
    path(
        "assay/scheduled-or-busy/<int:pk>",
        views.get_assay_is_busy_or_scheduled,
        name="assay_scheduled_or_busy",
    ),
    path(
        "assay/update/<int:pk>/", views.create_or_update_assay, name="update_assay"
    ),  # hard-coded in new.html
    path(
        "assay/delete/<int:pk>/", views.delete_assay, name="delete_assay"
    ),  # hard-coded in new.html
    path(
        "assay/<int:assay_id>/answer/",
        views.answer_assay_questions,
        name="answer_assay_questions",
    ),
]
# Version History

urlpatterns += [
    path(
        "assay/<int:assay_id>/answer/question/<int:question_id>/version-history/",
        views.get_version_history,
        name="get_version_history",
    ),
]
# Exports

urlpatterns += [
    path(
        "assay/<int:assay_id>/answer/time-sync/",
        views.assay_time_sync,
        name="assay_time_sync",
    ),
    path(
        "assay/<int:assay_id>/answer/hasfeedback/",
        views.assay_hasfeedback,
        name="assay_hasfeedback",
    ),
    path(
        "assay/<int:assay_id>/answer/feedback/",
        views.assay_feedback,
        name="assay_feedback",
    ),
    path(
        "assay/<int:assay_id>/answer/export/<str:export_type>/",
        views.export_assay,
        name="export_assay",
    ),
]

# Filter Investigation and Study for the first menu on new.html
# (so we only show hierachical options and not all)

urlpatterns += [
    # Other paths
    path(
        "filter-studies-by-investigation/<int:investigation_id>/",
        views.get_filtered_studies,
        name="filter_studie_by_investigation",
    ),
    path(
        "filter-assays-by-study/<int:study_id>/",
        views.get_filtered_assays,
        name="filter_assays_by_study",
    ),
]

# workspace URLs
urlpatterns += [
    path("workspace/create/", views.create_or_update_workspace, name="create_workspace"),
    path("workspace/update/<int:pk>/", views.create_or_update_workspace, name="update_workspace"),
    path("workspace/delete/<int:pk>/", views.delete_workspace, name="delete_workspace"),
    path("workspace/<int:pk>/member/add/", views.add_workspace_member, name="add_workspace_member"),
    path(
        "workspace/<int:pk>/member/add-email/",
        views.add_workspace_member_by_email,
        name="add_workspace_member_by_email",
    ),
    path(
        "workspace/<int:pk>/member/remove-email/",
        views.remove_workspace_member_by_email,
        name="remove_workspace_member_by_email",
    ),
    path(
        "workspace/<int:pk>/member/<int:user_id>/remove/",
        views.remove_workspace_member,
        name="remove_workspace_member",
    ),
    path("workspace/<int:pk>/assay/add/", views.add_workspace_assay, name="add_workspace_assay"),
    path(
        "workspace/<int:pk>/assay/<int:assay_id>/remove/",
        views.remove_workspace_assay,
        name="remove_workspace_assay",
    ),
]

urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
