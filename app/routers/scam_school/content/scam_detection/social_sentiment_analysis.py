"""social-sentiment-analysis module."""

from __future__ import annotations

social_sentiment_analysis = {
    "title": "Social Sentiment Analysis",
    "type": "interactive",
    "duration": 15,
    "xp": 200,
    "content": {
        "slides": [
            {"type": "title", "text": "The Bot Army", "sub": "Detecting fake engagement and coordinated shilling"},
            {
                "type": "interactive",
                "title": "Bot Detection Lab",
                "description": "Analyze a Twitter thread about a new token: 80% of replies are from accounts created in the last 30 days, all using similar phrases, and all following the same 3 accounts. Learn the telltale signs of bot networks.",
            },
            {
                "type": "quiz",
                "question": "What is 'astroturfing' in crypto social media?",
                "options": [
                    "Growing actual grass on a token logo",
                    "Creating fake grassroots support using bots and paid accounts to simulate organic enthusiasm",
                    "Launching a token on Astroport",
                    "A type of blockchain consensus",
                ],
                "correct": 1,
                "explanation": "Astroturfing is the practice of manufacturing fake organic support. Scammers create hundreds of accounts to hype a token, making it look like a genuine community movement when it's actually a coordinated marketing operation.",
            },
            {
                "type": "interactive",
                "title": "Coordinated Shilling Patterns",
                "description": "Track a token's mentions across Twitter, Telegram, and Discord over 72 hours. Notice the identical 'spikes' — hundreds of posts within the same 10-minute window, all using similar language. This is a paid shill campaign, not organic interest.",
            },
            {
                "type": "quiz",
                "question": "A Telegram group for a new token has 50K members but only 20 people are actually chatting. What does this suggest?",
                "options": [
                    "Most people prefer to just read",
                    "The member count is likely inflated with bots — real engagement is minimal",
                    "The group is very well organized",
                    "Everyone is in a different time zone",
                ],
                "correct": 1,
                "explanation": "High member count with low engagement is a classic sign of bot-inflated numbers. A genuine 50K community would have hundreds of active chatters. Always check the ratio of members to active participants.",
            },
        ]
    },
}

__all__ = ["social_sentiment_analysis"]
