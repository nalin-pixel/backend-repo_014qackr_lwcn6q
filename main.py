import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PromptRequest(BaseModel):
    prompt: str = Field(..., description="Natural language description of the desired house")
    width: float = Field(12.0, gt=4, lt=200, description="Overall footprint width in meters")
    depth: float = Field(10.0, gt=4, lt=200, description="Overall footprint depth in meters")
    floors: int = Field(1, ge=1, le=3, description="Number of floors")


class Room(BaseModel):
    name: str
    x: float
    y: float
    z: float
    width: float
    depth: float
    height: float


class GenerationResponse(BaseModel):
    footprint: dict
    rooms: List[Room]
    meta: dict


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    
    try:
        # Try to import database module
        from database import db
        
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            
            # Try to list collections to verify connectivity
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]  # Show first 10 collections
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
            
    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    
    # Check environment variables
    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    
    return response


def parse_program_from_prompt(prompt: str):
    """Very lightweight intent parser for bedrooms, bathrooms, kitchen size, style.
    Not an AI model, but good enough to shape a procedural layout.
    """
    text = prompt.lower()
    import re
    def find_num(keyword, default):
        m = re.search(rf"(\d+)\s*{keyword}", text)
        return int(m.group(1)) if m else default

    bedrooms = max(1, min(6, find_num("bed", 3)))
    bathrooms = max(1, min(4, find_num("bath", 2)))
    office = 1 if any(k in text for k in ["office", "study", "workspace"]) else 0
    open_plan = any(k in text for k in ["open plan", "open-plan", "open concept", "open layout"]) 
    style = "modern" if any(k in text for k in ["modern", "contemporary", "minimal"]) else (
        "traditional" if any(k in text for k in ["traditional", "classic"]) else "neutral"
    )
    return {
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "office": office,
        "open_plan": open_plan,
        "style": style,
    }


def split_rooms(width: float, depth: float, program: dict):
    """Procedurally split a rectangular footprint into simple rectangular rooms.
    Coordinates use meters; origin (0,0) at bottom-left. z=0 for ground floor.
    """
    wall = 0.1
    height = 3.0

    rooms: List[Room] = []

    # Reserve front strip for living/kitchen
    front_depth = depth * (0.45 if program["open_plan"] else 0.35)

    # Living area
    living = Room(name="Living", x=wall, y=wall, z=0, width=width - 2*wall, depth=front_depth - wall*2, height=height)
    rooms.append(living)

    # Kitchen + dining (share front zone in open plan)
    if program["open_plan"]:
        k_w = (width - 3*wall) * 0.4
        kitchen = Room(name="Kitchen", x=wall, y=wall, z=0, width=k_w, depth=front_depth - 2*wall, height=height)
        dining = Room(name="Dining", x=wall + k_w + wall, y=wall, z=0, width=width - (k_w + 3*wall), depth=front_depth - 2*wall, height=height)
        rooms.extend([kitchen, dining])
    else:
        k_depth = front_depth * 0.55
        kitchen = Room(name="Kitchen", x=wall, y=wall, z=0, width=(width - 3*wall) * 0.5, depth=k_depth - wall, height=height)
        dining = Room(name="Dining", x=wall + kitchen.width + wall, y=wall, z=0, width=width - (kitchen.width + 3*wall), depth=k_depth - wall, height=height)
        rooms.extend([kitchen, dining])

    # Back zone for private rooms
    remaining_depth = depth - front_depth - wall
    rows = max(1, program["bedrooms"] // 2)
    cols = 2 if program["bedrooms"] >= 2 else 1
    cell_w = (width - (cols + 1)*wall) / cols
    cell_d = (remaining_depth - (rows + 1)*wall) / rows if rows > 0 else remaining_depth

    placed_beds = 0
    y0 = front_depth + wall
    for r in range(rows):
        x0 = wall
        for c in range(cols):
            if placed_beds >= program["bedrooms"]:
                break
            room = Room(name=f"Bedroom {placed_beds+1}", x=x0, y=y0, z=0, width=cell_w, depth=cell_d, height=height)
            rooms.append(room)
            x0 += cell_w + wall
            placed_beds += 1
        y0 += cell_d + wall

    # Bathrooms: small rooms near back-right
    for i in range(program["bathrooms"]):
        b_w = min(2.2, cell_w * 0.6)
        b_d = min(2.4, wall + cell_d * 0.6)
        bx = width - b_w - wall
        by = front_depth + wall + i * (b_d + wall)
        rooms.append(Room(name=f"Bath {i+1}", x=bx, y=by, z=0, width=b_w, depth=b_d, height=height))

    # Optional office near entrance
    if program["office"]:
        o_w = min(3.0, (width - 3*wall) * 0.35)
        o_d = min(3.0, (front_depth - 3*wall) * 0.7)
        rooms.append(Room(name="Office", x=width - o_w - wall, y=wall, z=0, width=o_w, depth=o_d, height=height))

    return rooms


@app.post("/api/generate", response_model=GenerationResponse)
def generate_layout(req: PromptRequest):
    try:
        program = parse_program_from_prompt(req.prompt)
        rooms = split_rooms(req.width, req.depth, program)
        footprint = {"width": req.width, "depth": req.depth, "height": 3.0}
        meta = {"program": program, "note": "Procedurally generated layout (conceptual). Units in meters."}
        return GenerationResponse(footprint=footprint, rooms=rooms, meta=meta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
