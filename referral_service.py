"""
Referral Service
Handles all referral logic: code generation, credit distribution, tier progression
"""

from models import db, User, Referral, ReferralReward, REFERRAL_TIERS
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ReferralService:
    """Service for managing referral system"""
    
    @staticmethod
    def process_signup_referral(new_user, referral_code):
        """
        Process referral when new user signs up with a code
        
        Args:
            new_user: The newly created User object
            referral_code: The referral code they used
            
        Returns:
            dict: Result with success status and credits awarded
        """
        try:
            # Find referrer by code
            referrer = User.query.filter_by(referral_code=referral_code).first()
            
            if not referrer:
                logger.warning(f"Invalid referral code: {referral_code}")
                return {'success': False, 'error': 'Invalid referral code'}
            
            # Can't refer yourself
            if referrer.id == new_user.id:
                logger.warning(f"User {new_user.id} tried to refer themselves")
                return {'success': False, 'error': 'Cannot refer yourself'}
            
            # Check if referral already exists
            existing = Referral.query.filter_by(referee_id=new_user.id).first()
            if existing:
                logger.warning(f"User {new_user.id} already has a referral record")
                return {'success': False, 'error': 'User already referred'}
            
            # Create referral record
            referral = Referral(
                referrer_id=referrer.id,
                referee_id=new_user.id,
                referral_code=referral_code,
                signup_date=datetime.utcnow()
            )
            db.session.add(referral)
            
            # Update user referral info
            new_user.referred_by_code = referral_code
            new_user.referred_by_user_id = referrer.id
            
            # Award credits to BOTH users immediately
            referee_credits = 2  # New user gets 2 credits instead of 1
            referrer_credits = 3  # Referrer gets 3 credits
            
            # Give new user bonus credit (2 total, they already have 1 from signup)
            new_user.analysis_credits += 1  # Add 1 more to make it 2 total
            
            # Give referrer 3 credits
            referrer.analysis_credits += referrer_credits
            referrer.total_referrals += 1
            referrer.referral_credits_earned += referrer_credits
            
            # Create reward records
            # Reward for referee (new user)
            referee_reward = ReferralReward(
                user_id=new_user.id,
                referral_id=referral.id,
                reward_type='signup_bonus',
                credits_awarded=1,  # The +1 bonus
                description=f"Signup bonus from {referrer.name or referrer.email}'s referral"
            )
            db.session.add(referee_reward)
            
            # Reward for referrer
            referrer_reward = ReferralReward(
                user_id=referrer.id,
                referral_id=referral.id,
                reward_type='signup',
                credits_awarded=referrer_credits,
                description=f"Referred {new_user.name or new_user.email}"
            )
            db.session.add(referrer_reward)
            
            # Mark referral as credited
            referral.credits_awarded = True
            
            # Check for tier progression
            tier_result = ReferralService.check_tier_progression(referrer)
            
            db.session.commit()
            
            logger.info(f"✅ Referral processed: {referrer.email} → {new_user.email}")
            
            return {
                'success': True,
                'referee_credits': 2,
                'referrer_credits': referrer_credits,
                'referrer_name': referrer.name or referrer.email.split('@')[0],
                'tier_unlocked': tier_result.get('tier_unlocked'),
                'bonus_credits': tier_result.get('bonus_credits', 0)
            }
            
        except Exception as e:
            logger.error(f"Error processing referral: {str(e)}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def check_tier_progression(user):
        """
        Check if user has unlocked a new tier and award bonus credits
        
        Args:
            user: User object
            
        Returns:
            dict: tier_unlocked, bonus_credits
        """
        try:
            current_tier = user.referral_tier
            new_tier = current_tier
            
            # Determine new tier based on total referrals
            for tier, config in sorted(REFERRAL_TIERS.items(), reverse=True):
                if user.total_referrals >= config['referrals_required']:
                    new_tier = tier
                    break
            
            # If tier increased, award bonus credits
            if new_tier > current_tier:
                tier_config = REFERRAL_TIERS[new_tier]
                bonus_credits = tier_config['bonus_credits']
                
                if bonus_credits > 0:
                    # Award bonus credits
                    user.analysis_credits += bonus_credits
                    user.referral_credits_earned += bonus_credits
                    user.referral_tier = new_tier
                    
                    # Create reward record
                    tier_reward = ReferralReward(
                        user_id=user.id,
                        reward_type='tier_bonus',
                        credits_awarded=bonus_credits,
                        tier=new_tier,
                        description=f"Tier {new_tier} ({tier_config['name']}) unlock bonus"
                    )
                    db.session.add(tier_reward)
                    
                    logger.info(f"🎉 User {user.email} unlocked Tier {new_tier}: +{bonus_credits} credits")
                    
                    return {
                        'tier_unlocked': new_tier,
                        'tier_name': tier_config['name'],
                        'tier_icon': tier_config['icon'],
                        'bonus_credits': bonus_credits
                    }
            
            return {'tier_unlocked': None, 'bonus_credits': 0}
            
        except Exception as e:
            logger.error(f"Error checking tier progression: {str(e)}")
            return {'tier_unlocked': None, 'bonus_credits': 0}
    
    @staticmethod
    def get_referral_url(user, base_url='https://getofferwise.ai'):
        """Generate referral URL for user"""
        if not user.referral_code:
            user.generate_referral_code()
            db.session.commit()
        
        return f"{base_url}/?ref={user.referral_code}"
    
    @staticmethod
    def get_share_text(user):
        """Generate share text for social media"""
        name = user.name or "I"
        return {
            'twitter': f"🏡 {name} use OfferWise AI to analyze properties before making offers! Get 2 free analyses with my referral code: {user.referral_code}\n\n{ReferralService.get_referral_url(user)}\n\n#RealEstate #SmartBuyer",
            
            'facebook': f"Hey friends! I've been using OfferWise AI to analyze properties and it's been super helpful. You can try it with 2 free analyses using my referral code: {user.referral_code}\n\n{ReferralService.get_referral_url(user)}",
            
            'email_subject': "Check out OfferWise - Get 2 Free Property Analyses",
            
            'email_body': f"""Hi there!

I've been using OfferWise AI to analyze properties before making offers, and it's been incredibly helpful. It uses AI to cross-reference seller disclosures with inspection reports and gives you a detailed risk assessment.

You can try it out with 2 free analyses using my referral code: {user.referral_code}

Sign up here: {ReferralService.get_referral_url(user)}

Hope it helps with your property search!

Best,
{user.name or 'A friend'}""",
            
            'whatsapp': f"🏡 Check out OfferWise AI for property analysis! Get 2 free analyses with my code: {user.referral_code}\n\n{ReferralService.get_referral_url(user)}",
            
            'linkedin': f"I've been using OfferWise AI for property analysis and highly recommend it. AI-powered cross-reference analysis of seller disclosures and inspection reports. Get 2 free analyses with code: {user.referral_code}\n\n{ReferralService.get_referral_url(user)}"
        }
    
    @staticmethod
    def get_tier_info(tier_level):
        """Get information about a specific tier"""
        return REFERRAL_TIERS.get(tier_level, REFERRAL_TIERS[0])
    
    @staticmethod
    def calculate_total_earnings(user):
        """Calculate total potential earnings from referrals"""
        # Example: 25 referrals
        # (25 × 3) + 20 + 50 + 100 = 75 + 170 = 245 credits
        referrals = user.total_referrals
        
        earnings = referrals * 3  # Base earnings
        
        # Add tier bonuses
        if referrals >= 5:
            earnings += 20
        if referrals >= 10:
            earnings += 50
        if referrals >= 25:
            earnings += 100
        
        return earnings
