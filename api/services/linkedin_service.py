"""Service for finding job candidates on LinkedIn using real scraping."""
import logging
import asyncio
import json
import re
import os
import subprocess
import sys
from typing import List, Dict, Any, Optional, Tuple
from fastapi.concurrency import run_in_threadpool
from linkedin_jobs_scraper import LinkedinScraper
from linkedin_jobs_scraper.events import Events, EventData, EventMetrics
from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters
from linkedin_jobs_scraper.filters import (
    RelevanceFilters, TimeFilters, TypeFilters, ExperienceLevelFilters,
    OnSiteOrRemoteFilters, IndustryFilters, SalaryBaseFilters
)

from ..config import config
from ..utils import logger
from .rate_limiter import RateLimiter

class LinkedInService:
    """
    Service for finding candidates on LinkedIn based on job requirements.
    Uses linkedin-jobs-scraper with @sparticuz/chromium for serverless environments.
    """
    def __init__(self):
        # Rate limiter to prevent excessive calls
        self.rate_limiter = RateLimiter(5, 60_000)  # 5 requests per minute
        self.scraper = None
        self.li_at_cookie = None
        
        # Parse LinkedIn cookie from environment variable
        self._parse_linkedin_cookie()
        
        # Initialize the scraper
        self.init_scraper()
        
        # Storage for collected job data during scraping
        self.collected_jobs = []
        
    def _parse_linkedin_cookie(self):
        """Parse LinkedIn li_at cookie from the environment variable"""
        try:
            if config.linkedin_cookies_li_at:
                logger.info("Loading LinkedIn cookie from environment variable")
                
                # Directly use the value as the li_at cookie
                self.li_at_cookie = config.linkedin_cookies_li_at
                logger.debug("Successfully loaded LinkedIn li_at cookie")
            else:
                logger.warning("LINKEDIN_COOKIES_LI_AT environment variable not set")
                
        except Exception as e:
            logger.error(f"Error parsing LinkedIn cookie: {str(e)}")
            self.li_at_cookie = None
    
    def _setup_chromium_environment(self):
        """Set up the environment for @sparticuz/chromium"""
        logger.info("Setting up @sparticuz/chromium environment")
        
        try:
            # Set environment variables needed by @sparticuz/chromium
            # These help minimize issues when running in serverless environments
            os.environ["PUPPETEER_SKIP_CHROMIUM_DOWNLOAD"] = "true"
            os.environ["PUPPETEER_EXECUTABLE_PATH"] = "/tmp/chromium"
            
            # Increase the /tmp directory size on Lambda to ensure enough space for Chromium
            os.environ["PYTHONPATH"] = "/tmp"
            
            # Make Chrome executable directory if it doesn't exist
            if not os.path.exists("/tmp/chromium"):
                os.makedirs("/tmp/chromium", exist_ok=True)
            
            # Create nodejs script to get chrome executable path from @sparticuz/chromium
            chrome_script = """
            const chromium = require('@sparticuz/chromium');
            (async () => {
                console.log(await chromium.executablePath());
            })();
            """
            
            # Write the script to a file
            with open("/tmp/get_chrome_path.js", "w") as f:
                f.write(chrome_script)
            
            # Run the script to get Chrome path
            result = subprocess.run(
                ["node", "/tmp/get_chrome_path.js"], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            # Get the executable path
            chrome_path = result.stdout.strip()
            logger.info(f"Chrome executable path: {chrome_path}")
            
            # Return the path to the Chrome executable
            return chrome_path
            
        except Exception as e:
            logger.error(f"Error setting up Chromium: {str(e)}", exc_info=True)
            return None
        
    def init_scraper(self):
        """Initialize the LinkedIn scraper with authenticated session"""
        try:
            logger.info("Initializing LinkedIn scraper...")
            
            # Set the LI_AT_COOKIE environment variable for the scraper
            if self.li_at_cookie:
                logger.info("Setting LI_AT_COOKIE environment variable for LinkedIn scraper")
                os.environ["LI_AT_COOKIE"] = self.li_at_cookie
            else:
                logger.warning("No li_at cookie available. LinkedIn scraper may fail. Make sure LINKEDIN_COOKIES_LI_AT is set.")
            
            # Get path to Chrome binary from @sparticuz/chromium
            chrome_path = self._setup_chromium_environment()
            
            if not chrome_path:
                logger.error("Failed to get Chrome executable path")
                self.scraper = None
                return
                
            logger.info(f"Using Chrome binary at: {chrome_path}")
            
            # Configure the scraper with @sparticuz/chromium
            self.scraper = LinkedinScraper(
                chrome_executable_path=None,  # Let the scraper find chromedriver
                chrome_binary_location=chrome_path,  # Path to our @sparticuz/chromium binary
                chrome_options=None,
                headless=True,
                max_workers=1,  # Single worker to avoid rate limiting
                slow_mo=1.3,    # Higher value (1.3+) for authenticated sessions as per docs
                page_load_timeout=40
            )
            
            # Register event listeners
            self.scraper.on(Events.DATA, self._on_data)
            self.scraper.on(Events.ERROR, self._on_error)
            self.scraper.on(Events.END, self._on_end)
            
            logger.info("LinkedIn scraper initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize LinkedIn scraper: {str(e)}", exc_info=True)
            # Set to None so we can check if initialization failed
            self.scraper = None
    
    def _on_data(self, data: EventData):
        """Handle job data events from the scraper"""
        logger.debug(f"LinkedIn scraper got data: {data.title}, {data.company}")
        
        # Extract skills from description
        skills = self._extract_skills(data.description)
        
        # Format and store the job data
        job_data = {
            "job_id": data.job_id,
            "title": data.title,
            "company": data.company,
            "company_link": data.company_link,
            "location": data.place,
            "date_posted": data.date,
            "link": data.link,
            "apply_link": data.apply_link,
            "description": data.description,
            "extracted_skills": skills,
            "date_text": data.date_text,
            "insights": data.insights
        }
        
        self.collected_jobs.append(job_data)
    
    def _on_metrics(self, metrics: EventMetrics):
        """Handle metrics events from the scraper"""
        logger.debug(f"LinkedIn scraper metrics: {metrics}")
    
    def _on_error(self, error):
        """Handle error events from the scraper"""
        logger.error(f"LinkedIn scraper error: {error}")
    
    def _on_end(self):
        """Handle end events from the scraper"""
        logger.debug("LinkedIn scraper finished")
    
    def _extract_skills(self, text: str) -> List[str]:
        """Extract potential skills from job description text"""
        # Common tech skills to look for
        tech_skills = [
            "python", "java", "javascript", "c\\+\\+", "ruby", "php", "scala", "go", "rust",
            "react", "angular", "vue", "node\\.js", "django", "flask", "spring", "rails",
            "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ci/cd",
            "machine learning", "artificial intelligence", "data science", "big data",
            "sql", "nosql", "mongodb", "postgresql", "mysql", "oracle", "sql server",
            "agile", "scrum", "kanban", "devops", "sre", "tdd", "bdd"
        ]
        
        # Look for skills in the text
        found_skills = []
        for skill in tech_skills:
            if re.search(f"\\b{skill}\\b", text.lower()):
                # Clean up the skill name
                clean_skill = skill.replace("\\", "").replace("\\+\\+", "++")
                found_skills.append(clean_skill)
                
        return found_skills
    
    def _map_experience_level(self, experience_years_min: Optional[int]) -> List[ExperienceLevelFilters]:
        """Map experience years to LinkedIn experience level filters"""
        if not experience_years_min:
            return []
            
        if experience_years_min <= 1:
            return [ExperienceLevelFilters.INTERNSHIP, ExperienceLevelFilters.ENTRY_LEVEL]
        elif experience_years_min <= 3:
            return [ExperienceLevelFilters.ASSOCIATE]
        elif experience_years_min <= 5:
            return [ExperienceLevelFilters.MID_SENIOR]
        else:
            return [ExperienceLevelFilters.DIRECTOR]
    
    def _map_industry(self, industry: Optional[str]) -> List[IndustryFilters]:
        """Map industry string to LinkedIn industry filters"""
        if not industry:
            return []
            
        industry_map = {
            "technology": [IndustryFilters.TECHNOLOGY_INTERNET, IndustryFilters.IT_SERVICES, IndustryFilters.SOFTWARE_DEVELOPMENT],
            "finance": [IndustryFilters.FINANCIAL_SERVICES, IndustryFilters.BANKING, IndustryFilters.INVESTMENT_BANKING, IndustryFilters.INVESTMENT_MANAGEMENT],
            "aviation": [IndustryFilters.AIRLINES_AVIATION],
            "engineering": [IndustryFilters.CIVIL_ENGINEERING, IndustryFilters.ELECTRONIC_MANUFACTURING],
            "legal": [IndustryFilters.LEGAL_SERVICES],
            "automotive": [IndustryFilters.MOTOR_VEHICLES],
            "energy": [IndustryFilters.OIL_GAS],
            "recruiting": [IndustryFilters.STAFFING_RECRUITING],
            "environmental": [IndustryFilters.ENVIRONMENTAL_SERVICES],
            "gaming": [IndustryFilters.COMPUTER_GAMES],
            "information": [IndustryFilters.INFORMATION_SERVICES]
        }
        
        # Look for matches in the industry map
        industry_lower = industry.lower()
        for key, filters in industry_map.items():
            if key in industry_lower:
                return filters
                
        # Default to empty list if no match found
        return []
    
    async def find_candidates(self, search_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search for candidates on LinkedIn based on job requirements.
        
        Args:
            search_params: Dictionary containing search parameters
                
        Returns:
            Dictionary with candidates and metadata
        """
        logger.debug("LinkedInService: find_candidates called", extra={"params": search_params})
        
        # Apply rate limiting
        await self.rate_limiter.check()
        
        # Check if scraper was initialized successfully
        if not self.scraper:
            error_msg = "LinkedIn scraper not initialized. Please check if LINKEDIN_COOKIES_LI_AT is set correctly and ensure @sparticuz/chromium is properly configured."
            logger.error(error_msg)
            return {
                "error": "LinkedIn search failed",
                "message": error_msg,
                "candidates": [],
                "total_found": 0,
                "limit": search_params.get("limit", 10),
                "credits_used": 0,
                "cache_hits": 0
            }
        
        # Reset collected jobs for this search
        self.collected_jobs = []
        
        # Extract search parameters
        job_title = search_params.get("job_title", "")
        skills = search_params.get("skills", [])
        location = search_params.get("location", {})
        education = search_params.get("education", {})
        experience_years_min = search_params.get("experience_years_min")
        industry = search_params.get("industry", "")
        company_size = search_params.get("company_size", "")
        limit = min(search_params.get("limit", 10), 100)
        excluded_companies = search_params.get("excluded_companies", [])
        excluded_profiles = search_params.get("excluded_profiles", [])
        
        # Prepare location string for LinkedIn search
        locations = []
        if location:
            if location.get("country"):
                locations.append(location["country"])
            if location.get("region"):
                locations.append(location["region"])
            if location.get("city"):
                locations.append(location["city"])
        
        # Map industry string to LinkedIn industry filters
        industry_filters = self._map_industry(industry)
        
        # Set up query filters
        filters = QueryFilters(
            relevance=RelevanceFilters.RECENT,
            time=TimeFilters.MONTH,
            type=[TypeFilters.FULL_TIME],
            experience=self._map_experience_level(experience_years_min),
            industry=industry_filters if industry_filters else None
        )
        
        # Handle remote work preference if specified
        if location and location.get("remote", False):
            filters.on_site_or_remote = [OnSiteOrRemoteFilters.REMOTE]
        
        # If we have skills, add them to the query string
        query_text = job_title
        if skills and len(skills) > 0:
            primary_skills = skills[:3]  # Use up to 3 skills in the query
            skills_text = " ".join(primary_skills)
            query_text = f"{job_title} {skills_text}"
        
        # Create query
        query = Query(
            query=query_text,
            options=QueryOptions(
                locations=locations if locations else None,
                apply_link=True,
                skip_promoted_jobs=True,
                limit=limit * 2,  # Get more results to account for filtering
                filters=filters
            )
        )
        
        try:
            # Run the scraper in a thread pool to not block the async loop
            logger.info(f"Running LinkedIn scraper with query: {query_text}")
            await run_in_threadpool(lambda: self.scraper.run([query]))
            
            # Process collected jobs to extract candidate information
            candidates = []
            total_found = len(self.collected_jobs)
            
            logger.info(f"LinkedIn scraper found {total_found} jobs")
            
            # Filter jobs based on excluded companies
            filtered_jobs = [
                job for job in self.collected_jobs
                if not any(ex_company.lower() in job["company"].lower() for ex_company in excluded_companies)
            ]
            
            for job in filtered_jobs[:limit]:
                # Calculate relevance score based on job title and skills match
                relevance_score = self._calculate_relevance_score(job, search_params)
                
                # Create candidate entry from job data
                candidate = {
                    "name": f"Candidate at {job['company']}",  # LinkedIn jobs don't provide candidate names
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
            
            # Return the results
            return {
                "candidates": candidates[:limit],
                "total_found": total_found,
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
