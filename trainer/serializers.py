from rest_framework import serializers

from .services import build_public_profile_context


class LeaderboardRowSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    xp = serializers.IntegerField()
    level = serializers.IntegerField()
    streak_count = serializers.IntegerField()


class PublicProfileSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    total_xp = serializers.IntegerField()
    level = serializers.IntegerField()
    streak_count = serializers.IntegerField()
    learned_words_count = serializers.IntegerField()
    learned_languages = serializers.ListField(child=serializers.DictField())
    achievements = serializers.ListField(child=serializers.DictField())

    @classmethod
    def from_user(cls, user):
        context = build_public_profile_context(user)
        return {
            "user_id": user.id,
            "username": user.username,
            "total_xp": user.profile.total_xp,
            "level": context["level"],
            "streak_count": user.profile.streak_count,
            "learned_words_count": context["learned_words_count"],
            "learned_languages": context["learned_languages"],
            "achievements": [
                {
                    "code": item.achievement.code,
                    "title": item.achievement.title,
                    "description": item.achievement.description,
                    "unlocked_at": item.unlocked_at,
                }
                for item in context["earned_achievements"]
            ],
        }


class ReviewCountSerializer(serializers.Serializer):
    due_review_count = serializers.IntegerField()
