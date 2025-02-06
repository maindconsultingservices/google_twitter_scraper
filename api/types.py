from enum import Enum
from typing import List
from pydantic import BaseModel

class Tweet(BaseModel):
    """
    Minimal Tweet schema aligned to the actual fields we parse from twitter-api-client responses.
    """
    id: str
    userId: str
    username: str
    text: str
    conversationId: str
    timestamp: int  # in seconds
    permanentUrl: str

    # New fields capturing additional tweet stats
    quoteCount: int
    replyCount: int
    retweetCount: int


class SearchMode(str, Enum):
    Latest = "Latest"
    Top = "Top"
    People = "People"
    Photos = "Photos"
    Videos = "Videos"

class QueryTweetsResponse(BaseModel):
    tweets: List[Tweet]
