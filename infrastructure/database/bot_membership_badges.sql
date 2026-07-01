-- Bulletin Board Bot Membership & Dynamic Badge System
-- Run this in your Supabase SQL Editor to enable x402 bot tiers and profile badges.

-- 1. Add bot membership columns to profiles table
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS is_bot_member BOOLEAN DEFAULT false,
ADD COLUMN IF NOT EXISTS bot_tier TEXT DEFAULT 'none', -- 'none', 'scout', 'hunter', 'whale', 'api_plan'
ADD COLUMN IF NOT EXISTS bot_membership_expires_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS badges JSONB DEFAULT '[]'::jsonb;

-- 2. Create a table to track x402 bot subscription payments
CREATE TABLE IF NOT EXISTS public.bot_subscriptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    tier TEXT NOT NULL, -- 'scout', 'hunter', 'whale', 'api_plan'
    price_usd DECIMAL(10, 2) NOT NULL,
    price_atoms TEXT NOT NULL,
    calls_allocated INT NOT NULL,
    calls_used INT DEFAULT 0,
    status TEXT DEFAULT 'active', -- 'active', 'expired', 'cancelled'
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. Enable RLS on bot_subscriptions
ALTER TABLE public.bot_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own bot subscriptions"
    ON public.bot_subscriptions FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "System can insert bot subscriptions"
    ON public.bot_subscriptions FOR INSERT
    WITH CHECK (true); -- Handled by backend x402 enforcement

-- 4. Create a function to award badges dynamically
CREATE OR REPLACE FUNCTION public.award_badge(p_user_id UUID, p_badge_id TEXT, p_badge_name TEXT, p_badge_desc TEXT, p_badge_icon TEXT, p_badge_color TEXT)
RETURNS VOID AS $$
DECLARE
    v_badges JSONB;
    v_new_badge JSONB;
BEGIN
    -- Get current badges
    SELECT badges INTO v_badges FROM public.profiles WHERE id = p_user_id;

    -- Create new badge object
    v_new_badge := jsonb_build_object(
        'id', p_badge_id,
        'name', p_badge_name,
        'description', p_badge_desc,
        'icon', p_badge_icon,
        'color', p_badge_color,
        'awarded_at', NOW()
    );

    -- Check if badge already exists
    IF NOT (v_badges @> jsonb_build_array(v_new_badge)) THEN
        -- Append new badge
        UPDATE public.profiles
        SET badges = COALESCE(badges, '[]'::jsonb) || v_new_badge
        WHERE id = p_user_id;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5. Pre-populate some default badge templates (optional, for reference)
-- Badges are awarded via the `award_badge` function from the backend when conditions are met.
-- Example badge IDs: 'verified_bot', 'scam_hunter', 'paper_hands', 'diamond_hands', 'early_caller'

COMMENT ON COLUMN public.profiles.badges IS 'JSONB array of earned profile badges: [{id, name, description, icon, color, awarded_at}]';
COMMENT ON COLUMN public.profiles.bot_tier IS 'Current x402 bot membership tier: none, scout, hunter, whale, api_plan';
