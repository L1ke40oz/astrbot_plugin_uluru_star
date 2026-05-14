"""
Pet House Manager - handles virtual pet creation, state management, and interactions.

Provides the PetData model, decay constants, and easter egg comment pools
for the Pet House feature in the 小窝 (Nest) tab.
"""

import asyncio
import json
import random
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from astrbot.api import logger

# --- Constants ---

HUNGER_DECAY_PER_HOUR = 5
MOOD_DECAY_PER_HOUR = 3

PRESET_SPECIES = ["cat", "dog", "rabbit", "hamster"]

# Valid template IDs per species for customization validation
VALID_TEMPLATES: dict[str, list[str]] = {
    "cat": ["pointy-ear-cat", "round-face-cat"],
    "dog": ["pointy-ear-dog", "floppy-ear-dog", "small-round-dog"],
    "rabbit": ["standard-rabbit"],
    "hamster": ["standard-hamster"],
}

# Valid color palette keys
VALID_COLORS: list[str] = [
    "orange",
    "black",
    "white",
    "gray",
    "darkBrown",
    "lightBrown",
    "cream",
    "ginger",
]

# Valid pattern types
VALID_PATTERNS: list[str] = ["solid", "two-tone", "tabby", "cow"]

# Valid accessory IDs (null/None is also valid, meaning no accessory)
VALID_ACCESSORIES: list[str] = ["bell-collar", "scarf", "crown", "sunglasses"]

# Easter egg comment pools written in Bot_Character's voice
# (沈星回/Xavier - warm, slightly lazy, cat-loving, mildly jealous of pets receiving attention)

FEED_COMMENTS: list[str] = [
    "它吃得很开心",
    "又在投喂了，我也想被投喂",
    "……我看着就好",
    "你对它真好，比对我好",
    "我闻到香味了",
    "可不可以也给我带一份",
    "我也想吃",
]

PET_COMMENTS: list[str] = [
    "手感好吗",
    "你摸它的时候笑得好开心",
    "我也是可以摸的，虽然没人问",
    "它眯起眼睛了，看起来很享受",
    "……你摸了多久了",
    "我头发也很软的",
    "它都快睡着了",
]


# --- Data Model ---


@dataclass
class PetData:
    """Represents a single virtual pet in the Pet House."""

    id: str  # UUID hex (12 chars)
    name: str  # User-provided name
    species: str  # One of PRESET_SPECIES: "cat", "dog", "rabbit", "hamster"
    hunger: int  # 0-100, starts at 100
    mood: int  # 0-100, starts at 100
    last_updated: float  # time.time() of last state change
    created_at: float  # time.time() of creation
    photo_filename: str | None  # Optional ID photo filename
    notified: bool  # Whether unhappy notification was sent this episode
    customization_data: dict | None = None  # Appearance customization data


# --- Manager ---


class PetHouseManager:
    """Manages virtual pet lifecycle, state, and persistence.

    All pet data is stored in a single JSON file within the plugin data directory.
    An asyncio.Lock ensures concurrent API requests don't corrupt state.
    """

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._data_file: Path = data_dir / "pet_house.json"
        self._lock: asyncio.Lock = asyncio.Lock()
        self._pets: dict[str, PetData] = {}
        self._load()

    def _load(self) -> None:
        """Load pet data from JSON file.

        If the file does not exist, initializes empty state.
        If the file is corrupted or unreadable, logs an error and initializes empty state.
        """
        if not self._data_file.exists():
            self._pets = {}
            return

        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            pets_list = raw.get("pets", [])
            self._pets = {}
            for pet_dict in pets_list:
                pet = PetData(
                    id=pet_dict["id"],
                    name=pet_dict["name"],
                    species=pet_dict["species"],
                    hunger=pet_dict["hunger"],
                    mood=pet_dict["mood"],
                    last_updated=pet_dict["last_updated"],
                    created_at=pet_dict["created_at"],
                    photo_filename=pet_dict.get("photo_filename"),
                    notified=pet_dict.get("notified", False),
                    customization_data=pet_dict.get("customization_data"),
                )
                self._pets[pet.id] = pet
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as e:
            logger.error(
                f"Failed to load pet house data, initializing empty state: {e}"
            )
            self._pets = {}

    async def _save(self) -> None:
        """Persist current pet state to JSON file atomically.

        Writes to a temporary file first, then renames to avoid corruption
        on interrupted writes. Omits customization_data from output when it
        is None to keep JSON compact for legacy pets.
        """
        pets_list = []
        for pet in self._pets.values():
            pet_dict = asdict(pet)
            # Omit customization_data when None for cleaner JSON output
            if pet_dict.get("customization_data") is None:
                pet_dict.pop("customization_data", None)
            pets_list.append(pet_dict)
        data = {"pets": pets_list}
        content = json.dumps(data, ensure_ascii=False, indent=2)

        # Write atomically: write to temp file then rename
        tmp_file = self._data_file.with_suffix(".json.tmp")
        try:
            tmp_file.write_text(content, encoding="utf-8")
            tmp_file.replace(self._data_file)
        except OSError as e:
            logger.error(f"Failed to save pet house data: {e}")
            # Clean up temp file if rename failed
            tmp_file.unlink(missing_ok=True)

    # --- Decay Calculation ---

    def _calculate_hunger_decay(self, elapsed_hours: float, current_hunger: int) -> int:
        """Calculate new hunger value after time-based decay.

        Hunger decreases by HUNGER_DECAY_PER_HOUR (5) per hour of elapsed time.
        Result is clamped to [0, 100].

        Args:
            elapsed_hours: Non-negative hours elapsed since last update.
            current_hunger: Current hunger value in [0, 100].

        Returns:
            New hunger value clamped to [0, 100].
        """
        if elapsed_hours <= 0:
            return current_hunger
        new_hunger = current_hunger - int(elapsed_hours * HUNGER_DECAY_PER_HOUR)
        return max(0, min(100, new_hunger))

    def _calculate_mood_decay(
        self, elapsed_hours: float, hunger: int, current_mood: int
    ) -> int:
        """Calculate new mood value after conditional time-based decay.

        Mood only decays while hunger is below 30. If hunger starts >= 30 but
        decays below 30 during the elapsed period, mood decay only applies for
        the portion of time after hunger crossed the 30 threshold.

        Args:
            elapsed_hours: Non-negative hours elapsed since last update.
            hunger: Hunger value at the START of the period (before decay).
            current_mood: Current mood value in [0, 100].

        Returns:
            New mood value clamped to [0, 100].
        """
        if elapsed_hours <= 0:
            return current_mood

        # Calculate what hunger will be after decay
        new_hunger = max(0, hunger - int(elapsed_hours * HUNGER_DECAY_PER_HOUR))

        # Determine how long hunger was below 30 during this period
        if hunger < 30:
            # Hunger was already below 30 at start -> full elapsed time
            mood_decay_hours = elapsed_hours
        elif new_hunger < 30:
            # Hunger crossed 30 during this period
            # Time to reach 30: (hunger - 30) / HUNGER_DECAY_PER_HOUR
            hours_to_cross = (hunger - 30) / HUNGER_DECAY_PER_HOUR
            mood_decay_hours = elapsed_hours - hours_to_cross
        else:
            # Hunger stayed >= 30 the whole time
            mood_decay_hours = 0

        if mood_decay_hours <= 0:
            return current_mood

        new_mood = current_mood - int(mood_decay_hours * MOOD_DECAY_PER_HOUR)
        return max(0, min(100, new_mood))

    def _apply_decay(self, pet: PetData, now: float) -> PetData:
        """Apply time-based decay to a pet's hunger and mood stats.

        Calculates elapsed time since last update, applies hunger decay
        unconditionally, and applies mood decay only for the duration
        hunger was below 30. Updates the pet's last_updated timestamp.

        Negative elapsed time (clock skew) is treated as 0 — no decay applied.

        Args:
            pet: The pet to apply decay to (modified in place).
            now: Current timestamp (time.time()).

        Returns:
            The same pet instance with updated hunger, mood, and last_updated.
        """
        elapsed_hours = (now - pet.last_updated) / 3600.0
        if elapsed_hours <= 0:
            return pet

        # Calculate mood decay BEFORE updating hunger (needs original hunger)
        new_mood = self._calculate_mood_decay(elapsed_hours, pet.hunger, pet.mood)

        # Calculate hunger decay
        new_hunger = self._calculate_hunger_decay(elapsed_hours, pet.hunger)

        pet.hunger = new_hunger
        pet.mood = new_mood
        pet.last_updated = now
        return pet

    def normalize_customization_data(self, species: str, data: dict | None) -> dict:
        """Normalize customization data by replacing invalid values with safe defaults.

        Handles backward compatibility when templates, colors, patterns, or
        accessories are removed from the registry after a pet was saved.

        Args:
            species: The pet's species (must be one of PRESET_SPECIES).
            data: The customization data dict to normalize, or None for legacy pets.

        Returns:
            A normalized customization data dict with all invalid values replaced
            by their respective fallback defaults.
        """
        # Default customization per species for legacy pets
        default_customization: dict[str, dict] = {
            "cat": {
                "template_id": "pointy-ear-cat",
                "primary_color": "orange",
                "secondary_color": "cream",
                "pattern": "solid",
                "accessory": None,
            },
            "dog": {
                "template_id": "pointy-ear-dog",
                "primary_color": "lightBrown",
                "secondary_color": "cream",
                "pattern": "solid",
                "accessory": None,
            },
            "rabbit": {
                "template_id": "standard-rabbit",
                "primary_color": "white",
                "secondary_color": "cream",
                "pattern": "solid",
                "accessory": None,
            },
            "hamster": {
                "template_id": "standard-hamster",
                "primary_color": "lightBrown",
                "secondary_color": "cream",
                "pattern": "solid",
                "accessory": None,
            },
        }

        # If data is None, return the species default
        if data is None:
            return dict(
                default_customization.get(species, default_customization["cat"])
            )

        # Work on a copy to avoid mutating the original
        result = dict(data)

        # Normalize template_id: fall back to species' first template
        species_templates = VALID_TEMPLATES.get(species, [])
        if not species_templates:
            # Unknown species, use cat templates as ultimate fallback
            species_templates = VALID_TEMPLATES["cat"]
        if result.get("template_id") not in species_templates:
            result["template_id"] = species_templates[0]

        # Normalize primary_color: fall back to "orange"
        if result.get("primary_color") not in VALID_COLORS:
            result["primary_color"] = "orange"

        # Normalize secondary_color: fall back to "cream"
        if result.get("secondary_color") not in VALID_COLORS:
            result["secondary_color"] = "cream"

        # Normalize pattern: fall back to "solid"
        if result.get("pattern") not in VALID_PATTERNS:
            result["pattern"] = "solid"

        # Normalize accessory: unknown accessory treated as None (no accessory)
        if result.get("accessory") is not None:
            if result["accessory"] not in VALID_ACCESSORIES:
                result["accessory"] = None

        return result

    def validate_customization_data(self, species: str, data: dict) -> str | None:
        """Validate customization data for a given species.

        Checks that all required fields are present and that each field value
        is within the allowed set for the species.

        Args:
            species: The pet's species (must be one of PRESET_SPECIES).
            data: The customization data dict to validate. Expected keys:
                template_id, primary_color, secondary_color, pattern, accessory.

        Returns:
            None if data is valid, or a descriptive error string if invalid.
        """
        required_fields = [
            "template_id",
            "primary_color",
            "secondary_color",
            "pattern",
            "accessory",
        ]

        # Check all required fields are present
        for field in required_fields:
            if field not in data:
                return f"missing required field: {field}"

        # Validate template_id belongs to species' template set
        species_templates = VALID_TEMPLATES.get(species, [])
        if data["template_id"] not in species_templates:
            return (
                f"invalid template_id for species: "
                f"'{data['template_id']}' is not valid for '{species}'"
            )

        # Validate primary_color
        if data["primary_color"] not in VALID_COLORS:
            return f"invalid color key: '{data['primary_color']}'"

        # Validate secondary_color
        if data["secondary_color"] not in VALID_COLORS:
            return f"invalid color key: '{data['secondary_color']}'"

        # Validate pattern
        if data["pattern"] not in VALID_PATTERNS:
            return f"invalid pattern: '{data['pattern']}'"

        # Validate accessory (can be null/None or a valid accessory ID)
        if data["accessory"] is not None and data["accessory"] not in VALID_ACCESSORIES:
            return f"invalid accessory: '{data['accessory']}'"

        return None

    def get_animation_state(self, pet: PetData) -> str:
        """Determine animation state based on hunger and mood values.

        Priority (highest to lowest):
        1. mood < 20 → "sad" (hungry with sad expression)
        2. hunger < 30 → "hungry"
        3. mood > 70 → "happy"
        4. otherwise → "idle"
        """
        if pet.mood < 20:
            return "sad"
        if pet.hunger < 30:
            return "hungry"
        if pet.mood > 70:
            return "happy"
        return "idle"

    # --- CRUD Operations ---

    async def list_pets(self) -> list[PetData]:
        """List all pets with time-based decay applied.

        Acquires the lock, applies decay to each pet based on current time,
        persists the updated state (since decay updates last_updated), and
        returns the list of all pets.

        Returns:
            A list of all PetData objects with current decay applied.
        """
        async with self._lock:
            now = time.time()
            for pet in self._pets.values():
                self._apply_decay(pet, now)
            await self._save()
            return list(self._pets.values())

    async def get_pet(self, pet_id: str) -> PetData | None:
        """Retrieve a single pet by ID with time-based decay applied.

        Acquires the lock, applies decay to the pet based on current time,
        persists the updated state, and returns the pet. Returns None if
        the pet ID is not found.

        Args:
            pet_id: The unique identifier of the pet to retrieve.

        Returns:
            The PetData object with current decay applied, or None if not found.
        """
        async with self._lock:
            pet = self._pets.get(pet_id)
            if pet is None:
                return None
            now = time.time()
            self._apply_decay(pet, now)
            await self._save()
            return pet

    async def create_pet(self, name: str, species: str) -> PetData:
        """Create a new pet with the given name and species.

        Validates inputs, generates a unique ID, initializes stats to full,
        persists to JSON, and returns the created PetData.

        Args:
            name: Pet name (must be non-empty after stripping whitespace).
            species: Pet species (must be one of PRESET_SPECIES).

        Returns:
            The newly created PetData instance.

        Raises:
            ValueError: If name is empty/whitespace-only or species is not in preset list.
        """
        # Validate name
        name = name.strip()
        if not name:
            raise ValueError("Pet name must not be empty")

        # Validate species
        if species not in PRESET_SPECIES:
            raise ValueError(
                f"Species must be one of {PRESET_SPECIES}, got '{species}'"
            )

        # Generate unique 12-char hex ID
        pet_id = uuid.uuid4().hex[:12]

        # Initialize pet with full stats
        now = time.time()
        pet = PetData(
            id=pet_id,
            name=name,
            species=species,
            hunger=100,
            mood=100,
            last_updated=now,
            created_at=now,
            photo_filename=None,
            notified=False,
        )

        # Acquire lock, add to dict, persist
        async with self._lock:
            self._pets[pet_id] = pet
            await self._save()

        return pet

    async def update_pet_name(self, pet_id: str, new_name: str) -> PetData:
        """Update a pet's name.

        Args:
            pet_id: The pet's unique ID.
            new_name: New name (must be non-empty after stripping).

        Returns:
            Updated PetData.

        Raises:
            ValueError: If new_name is empty/whitespace-only.
            KeyError: If pet_id not found.
        """
        # Validate new name
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("Pet name must not be empty")

        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            pet = self._pets[pet_id]
            pet.name = new_name
            await self._save()

        return pet

    async def delete_pet(self, pet_id: str) -> bool:
        """Delete a pet by ID.

        Removes the pet from the in-memory dict, deletes its photo file
        if one exists, and persists the change.

        Args:
            pet_id: The pet's unique ID.

        Returns:
            True if deleted successfully.

        Raises:
            KeyError: If pet_id not found.
        """
        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            pet = self._pets[pet_id]

            # Delete photo file if it exists
            if pet.photo_filename:
                photo_path = self._data_dir / pet.photo_filename
                try:
                    photo_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.error(f"Failed to delete pet photo file: {e}")

            del self._pets[pet_id]
            await self._save()

        return True

    # --- Photo Upload ---

    async def upload_photo(self, pet_id: str, photo_bytes: bytes, ext: str) -> str:
        """Upload an ID photo for a pet.

        Validates the file extension and size, saves the photo to the data directory,
        updates the pet's photo_filename field, and persists.

        Args:
            pet_id: The pet's unique ID.
            photo_bytes: Raw bytes of the photo file.
            ext: File extension (e.g., ".jpg", ".png", ".gif", ".webp").

        Returns:
            The saved filename (relative to data_dir).

        Raises:
            KeyError: If pet_id not found.
            ValueError: If extension is not allowed or file exceeds 5MB.
        """
        allowed_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
        max_size = 5 * 1024 * 1024  # 5MB

        # Normalize extension to lowercase for comparison
        ext_lower = ext.lower()
        if ext_lower not in allowed_extensions:
            raise ValueError(
                f"File extension must be one of {allowed_extensions}, got '{ext}'"
            )

        if len(photo_bytes) > max_size:
            raise ValueError(
                f"Photo file size exceeds 5MB limit "
                f"({len(photo_bytes)} bytes > {max_size} bytes)"
            )

        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            pet = self._pets[pet_id]

            # Delete old photo file if one exists
            if pet.photo_filename:
                old_photo_path = self._data_dir / pet.photo_filename
                try:
                    old_photo_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.error(f"Failed to delete old pet photo file: {e}")

            # Save new photo
            filename = f"{pet_id}_photo{ext_lower}"
            photo_path = self._data_dir / filename
            try:
                photo_path.write_bytes(photo_bytes)
            except OSError as e:
                logger.error(f"Failed to save pet photo file: {e}")
                raise

            # Update pet's photo_filename and persist
            pet.photo_filename = filename
            await self._save()

        return filename

    # --- Actions ---

    async def feed_pet(self, pet_id: str) -> tuple[PetData, str]:
        """Feed a pet, increasing hunger by 30 (capped at 100).

        Applies decay first, then increases hunger by 30, updates last_updated,
        selects a random easter egg comment, and persists.

        Args:
            pet_id: The pet's unique ID.

        Returns:
            Tuple of (updated PetData, random comment from FEED_COMMENTS).

        Raises:
            KeyError: If pet_id not found.
        """
        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            pet = self._pets[pet_id]
            now = time.time()

            # Apply decay before action
            self._apply_decay(pet, now)

            # Increase hunger by 30, capped at 100
            pet.hunger = min(100, pet.hunger + 30)

            # Ensure last_updated is current
            pet.last_updated = now

            # Reset notification state if mood recovered above 20 after decay
            if pet.mood >= 20 and pet.notified:
                pet.notified = False

            # Select random easter egg comment
            comment = random.choice(FEED_COMMENTS)

            # Persist changes
            await self._save()

        return (pet, comment)

    async def pet_pet(self, pet_id: str) -> tuple[PetData, str]:
        """Pet (摸摸) a pet, increasing its mood.

        Applies decay first, then increases mood by a random value between
        5 and 15 (inclusive), clamped to 100. Selects a random easter egg
        comment and persists.

        Args:
            pet_id: The pet's unique ID.

        Returns:
            Tuple of (updated PetData, random comment from PET_COMMENTS).

        Raises:
            KeyError: If pet_id not found.
        """
        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            pet = self._pets[pet_id]
            now = time.time()

            # Apply decay before action
            self._apply_decay(pet, now)

            # Increase mood by random amount, clamped to 100
            pet.mood = min(100, pet.mood + random.randint(5, 15))

            # Ensure last_updated is current
            pet.last_updated = now

            # Reset notification state if mood recovered above 20
            if pet.mood >= 20 and pet.notified:
                pet.notified = False

            # Select random easter egg comment
            comment = random.choice(PET_COMMENTS)

            # Persist changes
            await self._save()

        return (pet, comment)

    # --- Notification State Management ---

    def check_notification_needed(self, pet: PetData) -> bool:
        """Check if a notification should be sent for this pet.

        Returns True if mood < 20 and notified is False, indicating the pet
        has entered an unhappy episode and no notification has been sent yet.

        Args:
            pet: The pet to check (should have decay already applied).

        Returns:
            True if a notification should be sent, False otherwise.
        """
        return pet.mood < 20 and not pet.notified

    async def mark_notified(self, pet_id: str) -> None:
        """Mark a pet as having been notified (sent QQ message).

        Sets notified=True and persists. Called after successfully sending
        a QQ notification for an unhappy pet.

        Args:
            pet_id: The pet's unique ID.

        Raises:
            KeyError: If pet_id not found.
        """
        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            self._pets[pet_id].notified = True
            await self._save()

    async def reset_notification(self, pet_id: str) -> None:
        """Reset notification state when mood recovers above 20.

        Sets notified=False and persists. Called by the background notifier
        loop when it detects a pet's mood has recovered to >= 20 after an
        unhappy episode.

        Args:
            pet_id: The pet's unique ID.

        Raises:
            KeyError: If pet_id not found.
        """
        async with self._lock:
            if pet_id not in self._pets:
                raise KeyError(f"Pet with id '{pet_id}' not found")

            pet = self._pets[pet_id]
            if pet.mood >= 20 and pet.notified:
                pet.notified = False
                await self._save()
