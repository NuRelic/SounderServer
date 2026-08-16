"""Canonical La-Mulana 2 data.

Read from the official wiki (https://la-mulana2.fandom.com) on 2026-08-16:
Category:Fields, Category:Guardians, Treasures, Mantras, and Applications.

Anything the wiki does not state outright is left out rather than guessed. That
covers Ankh Jewels (no total is given anywhere, and secondary sources split
between 9 and 10) and Holy Grail warp points (never enumerated). App
combinations are known but deliberately absent: working them out is the game.

Every checklist group accepts user-added rows, because this list is a starting
point and not a claim to be complete.
"""

# Ordered in five bands rather than a precise progression order, which varies
# by route. The Village first, then the nine "frontside" fields -- identified as
# frontside by each holding one of the ten Sacred Orbs -- then the later fields,
# the connecting sub-areas, and the La-Mulana ruins revisited in the back half.
AREAS = [
    "Village of Departure",

    "Roots of Yggdrasil",
    "Annwfn",
    "Immortal Battlefield",
    "Icefire Treetop",
    "Divine Fortress",
    "Shrine of the Frost Giants",
    "Gate of the Dead",
    "Takamagahara Shrine",
    "Heaven's Labyrinth",

    "Valhalla",
    "Dark Star Lord's Mausoleum",
    "Ancient Chaos",
    "Hall of Malice",
    "Eternal Prison",
    "Eternal Prison - Gloom",
    "Nibiru",
    "Spiral Hell",

    "Altar",
    "Cavern",
    "Cliff",
    "Corridor of Blood",
    "The Tower of Oannes",

    "Gate of Guidance",
    "Mausoleum of the Giants",
    "Endless Corridor",
    "Gate of Illusion",
    "Inferno Cavern",
]

# (group name, [row names]) in display order. Row names carry the field and map
# coordinate where the wiki gives one, because "which orb am I missing and
# where" is the question these lists exist to answer -- the in-game menu already
# tells you the count.
CHECKLIST = [
    ("Guardians", [
        "Fafnir — Roots of Yggdrasil",
        "Vritra — Valhalla",
        "Kujata — Annwfn",
        "Aten-Ra — Dark Star Lord's Mausoleum",
        "Jormungand — Immortal Battlefield",
        "Anu — Ancient Chaos",
        "Surtr — Icefire Treetop",
        "Echidna — Hall of Malice",
        "Hel — Eternal Prison",
        "9th Child — Spiral Hell",
    ]),
    ("Sacred Orbs", [
        "Village of Departure (G-3)",
        "Roots of Yggdrasil (E-4)",
        "Annwfn (E-5)",
        "Immortal Battlefield (F-6)",
        "Icefire Treetop (F-4)",
        "Divine Fortress (B-3)",
        "Shrine of the Frost Giants (C-2)",
        "Gate of the Dead (B-4)",
        "Takamagahara Shrine (D-5)",
        "Heaven's Labyrinth (C-2)",
    ]),
    ("Mantras", [
        "Himinn — Divine Fortress (D-5)",
        "Iorð — Annwfn (D-4)",
        "Sól — Cavern (B-1)",
        "Máni — Immortal Battlefield (E-7)",
        "Sær — Shrine of the Frost Giants (C-3)",
        "Eldr — Valhalla (D-1)",
        "Vindr — Ancient Chaos (D-5)",
        "Móðir — Eternal Prison - Gloom (C-5)",
        "Barn — Inferno Cavern (A-1)",
        "Nótt — Nibiru (B-2)",
    ]),
    ("Maps", [
        # The wiki's map list labels the Eternal Prison's Doom and Gloom
        # halves separately (below), which is why those two rows don't match
        # the single "Eternal Prison" entry in AREAS -- not a typo.
        "Village of Departure / La-Mulana Ruins — from Nebur or Xelpud",
        "Roots of Yggdrasil (E-3)",
        "Annwfn (E-3)",
        "Immortal Battlefield (F-2)",
        "Icefire Treetop (B-2)",
        "Divine Fortress (D-4)",
        "Shrine of the Frost Giants (D-5)",
        "Gate of the Dead (D-3)",
        "Takamagahara Shrine (D-2)",
        "Heaven's Labyrinth (C-5)",
        "Valhalla (A-3)",
        "Dark Star Lord's Mausoleum (C-6)",
        "Ancient Chaos (B-6)",
        "Hall of Malice (C-1)",
        "Eternal Prison - Doom (D-5)",
        "Eternal Prison - Gloom (E-2)",
    ]),
    ("Apps", [
        "Xelputter",
        "Yagoo Map Reader",
        "Yagoo Map Street",
        "TextTrax 2",
        "Ruins Encyclopedia",
        "Mantra",
        "Guild",
        "Kosugi Research Papers",
        "Enga Musica",
        "Beo Eg-Lana",
        "Alert",
        "Snapshots",
        "Skull",
        "Race Scanner",
        "Death Village",
        "Rose and Camellia",
        "Space Capstar II",
        "Lonely House Moving",
        "Mekuri Master",
        "Bounce Shot",
        "Miracle Witch",
        "Future Development Company",
        "La-Mulana",
        "La-Mulana 2",
    ]),
]
