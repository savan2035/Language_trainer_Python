from django.urls import path

from .api_views import LeaderboardApiView, PublicProfileApiView, ReviewCountApiView
from .views import (
    DashboardView,
    DeckListView,
    ProfileView,
    PublicProfileView,
    LeaderboardView,
    deck_create,
    deck_edit,
    deck_import_csv,
    home,
    review_result,
    review_session,
    review_start,
    training_result,
    training_session,
    training_start,
)


urlpatterns = [
    path("", home, name="home"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("leaderboard/", LeaderboardView.as_view(), name="leaderboard"),
    path("profiles/<int:user_id>/", PublicProfileView.as_view(), name="public-profile"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("decks/", DeckListView.as_view(), name="deck-list"),
    path("decks/new/", deck_create, name="deck-create"),
    path("decks/import/", deck_import_csv, name="deck-import"),
    path("decks/<int:deck_id>/edit/", deck_edit, name="deck-edit"),
    path("decks/<int:deck_id>/<str:mode>/start/", training_start, name="study-start"),
    path("decks/<int:deck_id>/<str:mode>/session/", training_session, name="study-session"),
    path("decks/<int:deck_id>/<str:mode>/result/", training_result, name="study-result"),
    path("review/start/", review_start, name="review-start"),
    path("review/session/", review_session, name="review-session"),
    path("review/result/", review_result, name="review-result"),
    path("api/leaderboard/", LeaderboardApiView.as_view(), name="api-leaderboard"),
    path("api/profile/<int:user_id>/", PublicProfileApiView.as_view(), name="api-public-profile"),
    path("api/training/review-count/", ReviewCountApiView.as_view(), name="api-review-count"),
]
