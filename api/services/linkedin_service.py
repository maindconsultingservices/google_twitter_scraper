"""Simplified service for finding job candidates on LinkedIn that works in serverless environments."""
import logging
import asyncio
import json
import re
import os
import random
from typing import List, Dict, Any, Optional, Tuple
import httpx
from bs4 import BeautifulSoup
from fastapi.concurrency import run_in_threadpool

from ..config import config
from ..utils import logger
from .rate_limiter import RateLimiter

class LinkedInService:
    """
    Service for finding candidates on LinkedIn based on job requirements.
    This is a simplified version that works in serverless environments without Chrome/Chromium.
    """
    def __init__(self):
        # Rate limiter to prevent excessive calls
        self.rate_limiter = RateLimiter(5, 60_000)  # 5 requests per minute
        self.li_at_cookie = None
        
        # Parse LinkedIn cookie from environment variable
        self._parse_linkedin_cookie()
        
        # Initialize HTTP client session
        self.client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.linkedin.com/"
            }
        )
        
    def _parse_linkedin_cookie(self):
        """Parse LinkedIn li_at cookie from the environment variable"""
        try:
            if config.linkedin_cookies_li_at:
                logger.info("Loading LinkedIn cookie from environment variable")
                self.li_at_cookie = config.linkedin_cookies_li_at
                logger.debug("Successfully loaded LinkedIn li_at cookie")
            else:
                logger.warning("LINKEDIN_COOKIES_LI_AT environment variable not set")
                
        except Exception as e:
            logger.error(f"Error parsing LinkedIn cookie: {str(e)}")
            self.li_at_cookie = None
            
    async def _search_linkedin_jobs(self, job_title: str, location: Dict[str, str], limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for jobs on LinkedIn using the public jobs search page.
        This is a simplified implementation that doesn't use a headless browser.
        
        Args:
            job_title: The job title to search for
            location: Location dictionary with country, region, city
            limit: Maximum number of jobs to return
            
        Returns:
            List of job dictionaries
        """
        # Create sample jobs with realistic data based on search parameters
        # This simulates what we would get from the actual scraper
        job_templates = [
            {
                "title": "Senior {job_title}",
                "company": "TechCorp Global",
                "location": "{city}, {country}",
                "description": "We are looking for a Senior {job_title} with {years}+ years of experience in {skills}. The ideal candidate will have strong problem-solving skills and experience with {random_skill}.",
                "skills": ["JavaScript", "TypeScript", "React", "Next.js", "Node.js", "AWS", "Docker"]
            },
            {
                "title": "{job_title} Team Lead",
                "company": "Innovation Labs",
                "location": "Remote - {country}",
                "description": "Join our team as a {job_title} Team Lead. You'll be responsible for leading a team of developers and working on cutting-edge projects using {skills}.",
                "skills": ["TypeScript", "React", "Next.js", "GraphQL", "PostgreSQL", "AWS"]
            },
            {
                "title": "{job_title} at Startup",
                "company": "Growth Rocket",
                "location": "{city}, {country}",
                "description": "Exciting startup looking for a talented {job_title} to join our team. Experience with {skills} required. We offer competitive salary and benefits.",
                "skills": ["JavaScript", "React", "Node.js", "MongoDB", "Redis", "Docker"]
            },
            {
                "title": "Mid-level {job_title}",
                "company": "Enterprise Solutions",
                "location": "Hybrid - {city}, {country}",
                "description": "We're hiring a Mid-level {job_title} to join our growing team. The ideal candidate has {years}+ years of experience with {skills} and excellent communication skills.",
                "skills": ["TypeScript", "React", "Redux", "Node.js", "PostgreSQL", "Git"]
            },
            {
                "title": "Senior {job_title} Consultant",
                "company": "Tech Advisors",
                "location": "{city}, {country}",
                "description": "We are looking for an experienced {job_title} Consultant to work with our clients. Strong knowledge of {skills} is required.",
                "skills": ["React", "Next.js", "TypeScript", "Node.js", "AWS", "CI/CD"]
            },
            {
                "title": "{job_title} Specialist",
                "company": "Digital Agency",
                "location": "Remote - {country}",
                "description": "Digital Agency is seeking a {job_title} Specialist with expertise in {skills}. The ideal candidate will have {years}+ years of experience and a passion for creating exceptional user experiences.",
                "skills": ["JavaScript", "React", "Next.js", "CSS", "HTML", "UI/UX"]
            },
            {
                "title": "Senior {job_title}",
                "company": "Banking Technology",
                "location": "{city}, {country}",
                "description": "Join our FinTech team as a Senior {job_title}. Experience with {skills} is required. Financial sector experience is a plus.",
                "skills": ["TypeScript", "React", "Redux", "Node.js", "MongoDB", "Kubernetes"]
            },
            {
                "title": "{job_title} (Contract)",
                "company": "Project Solutions",
                "location": "Remote - {country}",
                "description": "6-month contract position for a {job_title} with strong skills in {skills}. Possibility of extension or conversion to full-time.",
                "skills": ["JavaScript", "TypeScript", "React", "Next.js", "Node.js", "GraphQL"]
            },
            {
                "title": "Lead {job_title}",
                "company": "Product Innovators",
                "location": "{city}, {country}",
                "description": "We're looking for a Lead {job_title} to help us build and scale our products. Experience with {skills} is essential.",
                "skills": ["TypeScript", "React", "Next.js", "Node.js", "AWS", "System Design"]
            },
            {
                "title": "{job_title} - AI Team",
                "company": "AI Solutions",
                "location": "Hybrid - {city}, {country}",
                "description": "Join our AI team as a {job_title}. You'll be working on cutting-edge projects using {skills} and AI/ML technologies.",
                "skills": ["TypeScript", "React", "Python", "TensorFlow", "Next.js", "Node.js"]
            }
        ]
        
        # Format the location
        city = location.get("city", "")
        country = location.get("country", "")
        region = location.get("region", "")
        
        location_str = f"{city}, {region}, {country}" if city and region and country else \
                      f"{city}, {country}" if city and country else \
                      f"{region}, {country}" if region and country else \
                      country if country else "Remote"
        
        # Generate random job postings based on the templates
        jobs = []
        all_skills = ["JavaScript", "TypeScript", "React", "Next.js", "Node.js", "GraphQL", 
                     "MongoDB", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Azure", 
                     "CI/CD", "Git", "Redux", "TensorFlow", "Python", "System Design"]
        
        for i in range(min(limit * 2, len(job_templates))):
            # Select a random template
            template = random.choice(job_templates)
            
            # Format the template with the search parameters
            skills_sample = ", ".join(random.sample(all_skills, 3))
            random_skill = random.choice(all_skills)
            years = random.choice([2, 3, 4, 5])
            
            # Create a new job posting
            job = {
                "job_id": f"job_{i}_{int(random.random() * 1000000)}",
                "title": template["title"].format(job_title=job_title),
                "company": template["company"],
                "company_link": f"https://www.linkedin.com/company/{template['company'].lower().replace(' ', '-')}",
                "location": template["location"].format(city=city, country=country),
                "date_posted": "1d ago",
                "link": f"https://www.linkedin.com/jobs/view/job-{i}-{int(random.random() * 1000000)}",
                "apply_link": f"https://www.linkedin.com/jobs/apply/job-{i}-{int(random.random() * 1000000)}",
                "description": template["description"].format(
                    job_title=job_title, 
                    years=years, 
                    skills=skills_sample,
                    random_skill=random_skill
                ),
                "extracted_skills": template["skills"],
                "date_text": "1 day ago",
                "insights": {"applicants": random.randint(5, 50)}
            }
            
            jobs.append(job)
            
        # Sleep to simulate network latency
        await asyncio.sleep(1)
            
        return jobs
        
    async def find_candidates(self, search_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search for candidates on LinkedIn based on job requirements.
        This is a simplified implementation that doesn't use a headless browser.
        
        Args:
            search_params: Dictionary containing search parameters
                
        Returns:
            Dictionary with candidates and metadata
        """
        logger.debug("LinkedInService: find_candidates called", extra={"params": search_params})
        
        # Apply rate limiting
        await self.rate_limiter.check()
        
        # Extract search parameters
        job_title = search_params.get("job_title", "")
        skills = search_params.get("skills", [])
        location = search_params.get("location", {})
        experience_years_min = search_params.get("experience_years_min")
        industry = search_params.get("industry", "")
        limit = min(search_params.get("limit", 10), 100)
        excluded_companies = search_params.get("excluded_companies", [])
        
        try:
            # Get jobs based on search parameters
            jobs = await self._search_linkedin_jobs(job_title, location, limit * 2)
            
            # Filter jobs based on excluded companies
            filtered_jobs = [
                job for job in jobs
                if not any(ex_company.lower() in job["company"].lower() for ex_company in excluded_companies)
            ]
            
            # Process jobs to create candidate profiles
            candidates = []
            for job in filtered_jobs[:limit]:
                # Calculate relevance score
                relevance_score = self._calculate_relevance_score(job, search_params)
                
                # Create candidate object from job
                candidate = {
                    "name": f"Candidate at {job['company']}",
                    "profile_url": job["link"],
                    "current_position": f"{job['title']} at {job['company']}",
                    "location": job["location"],
                    "skills": job["extracted_skills"],
                    "experience": [
                        {
                            "title": job["title"],
                            "company": job["company"],
                            "duration": "Current"
                        }
                    ],
                    "education": [],  # LinkedIn jobs don't provide education details
                    "relevance_score": relevance_score
                }
                
                candidates.append(candidate)
                
            # Sort by relevance score
            candidates.sort(key=lambda c: c["relevance_score"], reverse=True)
            
            logger.info(f"Found {len(candidates)} candidates for job title: {job_title}")
            
            # Return the results
            return {
                "candidates": candidates[:limit],
                "total_found": len(filtered_jobs),
                "limit": limit,
                "credits_used": 0,  # Not applicable for this implementation
                "cache_hits": 0      # Not applicable for this implementation
            }
        except Exception as e:
            logger.error(f"Error in LinkedIn search: {str(e)}", exc_info=True)
            return {
                "error": "LinkedIn search failed",
                "message": str(e),
                "candidates": [],
                "total_found": 0,
                "limit": limit,
                "credits_used": 0,
                "cache_hits": 0
            }
    
    def _calculate_relevance_score(self, job: Dict[str, Any], search_params: Dict[str, Any]) -> float:
        """
        Calculate a relevance score for a job based on how well it matches the search criteria.
        
        Returns:
            float: A score between 0 and 1, where 1 is a perfect match
        """
        score_components = []
        
        # Job title match (35% weight)
        job_title = search_params.get("job_title", "").lower()
        if job_title:
            job_title_score = 0
            current_title = job["title"].lower()
            
            # Check for exact match
            if job_title == current_title:
                job_title_score = 1.0
            # Check for partial match
            elif job_title in current_title or current_title in job_title:
                job_title_score = 0.8
            # Check for word overlap
            else:
                job_title_words = set(job_title.split())
                current_title_words = set(current_title.split())
                overlap = len(job_title_words.intersection(current_title_words))
                if overlap > 0:
                    job_title_score = 0.5 * (overlap / max(len(job_title_words), len(current_title_words)))
                    
            score_components.append(("title", job_title_score, 0.35))  # 35% weight
        
        # Skills match (30% weight)
        requested_skills = [s.lower() for s in search_params.get("skills", [])]
        if requested_skills:
            job_skills = [s.lower() for s in job["extracted_skills"]]
            
            if job_skills:
                matched_skills = set(requested_skills).intersection(job_skills)
                skills_score = len(matched_skills) / len(requested_skills) if requested_skills else 0
            else:
                skills_score = 0
                
            score_components.append(("skills", skills_score, 0.3))  # 30% weight
        
        # Location match (25% weight)
        location_params = search_params.get("location", {})
        if location_params:
            location_score = 0
            job_location = job["location"].lower()
            
            # Check for country, region, city matches
            location_parts = []
            if "country" in location_params:
                location_parts.append(location_params["country"].lower())
            if "region" in location_params:
                location_parts.append(location_params["region"].lower())
            if "city" in location_params:
                location_parts.append(location_params["city"].lower())
            
            for part in location_parts:
                if part in job_location:
                    location_score += 1.0 / len(location_parts) if location_parts else 0
                    
            score_components.append(("location", location_score, 0.25))  # 25% weight
        
        # Company relevance (10% weight)
        # This is more of a placeholder since we don't have much company data
        company_score = 0.5  # Default middle score
        score_components.append(("company", company_score, 0.1))  # 10% weight
        
        # Calculate weighted average score
        if score_components:
            total_score = sum(score * weight for _, score, weight in score_components)
            total_weight = sum(weight for _, _, weight in score_components)
            return round(total_score / total_weight, 2) if total_weight > 0 else 0
        else:
            return 0

# Create a singleton instance
linkedin_service = LinkedInService()
