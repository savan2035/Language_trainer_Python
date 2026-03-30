from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Profile
from .serializers import LeaderboardRowSerializer, PublicProfileSerializer, ReviewCountSerializer
from .services import get_due_review_count, get_leaderboard


class LeaderboardApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        period = request.query_params.get("period", "all_time")
        if period not in {"weekly", "all_time"}:
            period = "all_time"
        data = get_leaderboard(period)
        return Response(LeaderboardRowSerializer(data, many=True).data)


class PublicProfileApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id: int, *args, **kwargs):
        user = get_object_or_404(Profile.objects.select_related("user"), user_id=user_id).user
        payload = PublicProfileSerializer.from_user(user)
        return Response(PublicProfileSerializer(payload).data)


class ReviewCountApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        payload = {"due_review_count": get_due_review_count(request.user)}
        return Response(ReviewCountSerializer(payload).data)
