"""LP-65: the places in Lebanon a restaurant (or branch) can pick as its area.

LastPlate's own list, grouped by region, with the other spellings people
type, so "ham" finds Hamra and "ashrafieh" finds Achrafieh. Every restaurant
in the same neighbourhood gets exactly the same area name, which is what
customers filter by (LP-32).

Each entry: (name shown and saved, other spellings).
"""

import unicodedata

REGIONS = [
    ("Beirut", [
        ("Achrafieh", ["Ashrafieh", "Ashrafiyeh", "Achrafiyeh"]),
        ("Ain El Mreisseh", ["Ain Mreisseh", "Ain el Mraiseh"]),
        ("Badaro", []),
        ("Bachoura", ["Bashoura"]),
        ("Basta", []),
        ("Burj Abi Haidar", ["Borj Abi Haidar"]),
        ("Clemenceau", []),
        ("Downtown Beirut", ["Centre Ville", "Down Town", "Solidere", "Beirut Central District"]),
        ("Gemmayzeh", ["Gemayzeh", "Gemmayze", "Gemayze"]),
        ("Hamra", ["Hamra Street"]),
        ("Jnah", []),
        ("Karantina", ["Quarantina"]),
        ("Koraytem", ["Qoreitem", "Kraytem"]),
        ("Manara", []),
        ("Mar Elias", ["Mar Elias Street"]),
        ("Mar Mikhael", ["Mar Mkhayel", "Mar Michael"]),
        ("Mazraa", ["Mazraa Corniche"]),
        ("Monnot", []),
        ("Msaytbeh", ["Mseitbeh"]),
        ("Ramlet El Bayda", ["Ramlet al Bayda"]),
        ("Ras Beirut", []),
        ("Raouche", ["Rawcheh", "Rouche"]),
        ("Saifi", ["Saifi Village"]),
        ("Sanayeh", ["Sanayeh Garden"]),
        ("Sodeco", []),
        ("Tallet El Khayat", ["Tallet al Khayat"]),
        ("Tariq El Jdideh", ["Tarik Jdideh", "Tarik el Jdideh", "Tariq al Jadida"]),
        ("Verdun", []),
        ("Zokak El Blat", ["Zuqaq al Blat"]),
    ]),
    ("Beirut suburbs", [
        ("Baabda", []),
        ("Bir Hassan", []),
        ("Bourj Hammoud", ["Burj Hammoud", "Borj Hammoud"]),
        ("Burj El Barajneh", ["Bourj el Barajneh", "Borj el Brajneh"]),
        ("Chiyah", ["Shiyah"]),
        ("Choueifat", ["Shweifat"]),
        ("Dekwaneh", ["Dekweneh"]),
        ("Furn El Chebbak", ["Furn el Shebbak", "Forn el Chebbak"]),
        ("Ghobeiry", ["Ghobeiri"]),
        ("Hadath", ["Hadat"]),
        ("Haret Hreik", ["Haret Hreyk"]),
        ("Hazmieh", ["Hazmiyeh"]),
        ("Khaldeh", ["Khalde"]),
        ("Sin El Fil", ["Sin el Fil"]),
    ]),
    ("Metn", [
        ("Antelias", []),
        ("Baabdat", []),
        ("Beit Mery", ["Beit Meri"]),
        ("Bikfaya", []),
        ("Broummana", ["Brummana", "Broumana"]),
        ("Bsalim", []),
        ("Dbayeh", ["Dbaye"]),
        ("Dhour El Choueir", ["Dhour Shweir"]),
        ("Fanar", []),
        ("Jal El Dib", ["Jal el Dib"]),
        ("Jdeideh", ["Jdeideh el Metn", "Jdaide"]),
        ("Mansourieh", ["Mansouriyeh"]),
        ("Mtayleb", []),
        ("Naccache", ["Nakkash"]),
        ("Rabieh", ["Rabiyeh"]),
        ("Sad El Bouchrieh", ["Sad el Baouchrieh", "Sed el Bauchrieh"]),
        ("Zalka", []),
    ]),
    ("Keserwan", [
        ("Adma", []),
        ("Ajaltoun", []),
        ("Faraya", ["Faqra"]),
        ("Ghazir", []),
        ("Harissa", []),
        ("Jounieh", ["Jounie", "Juniyeh"]),
        ("Kaslik", []),
        ("Sarba", []),
        ("Tabarja", []),
        ("Zouk Mikael", ["Zouk Mekhayel"]),
        ("Zouk Mosbeh", ["Zouk Mosbah"]),
    ]),
    ("Jbeil", [
        ("Amchit", []),
        ("Blat", []),
        ("Byblos", ["Jbeil", "Jbail"]),
        ("Halat", []),
        ("Nahr Ibrahim", []),
    ]),
    ("Aley and Chouf", [
        ("Aley", ["Alay"]),
        ("Baakline", []),
        ("Barja", []),
        ("Beiteddine", ["Beit ed Dine", "Beit el Din"]),
        ("Bhamdoun", []),
        ("Chhim", ["Shhim"]),
        ("Damour", []),
        ("Deir El Qamar", ["Deir al Qamar"]),
        ("Jiyeh", []),
        ("Sofar", []),
        ("Souk El Gharb", ["Souq el Gharb"]),
    ]),
    ("North Lebanon", [
        ("Amioun", []),
        ("Batroun", []),
        ("Bcharre", ["Bsharri", "Bcharreh"]),
        ("Chekka", ["Shekka"]),
        ("Ehden", []),
        ("El Mina", ["Mina Tripoli"]),
        ("Enfeh", ["Anfeh"]),
        ("Halba", []),
        ("Koura", ["Kura"]),
        ("Tripoli", ["Trablos", "Tarablus"]),
        ("Zgharta", []),
    ]),
    ("South Lebanon", [
        ("Bint Jbeil", []),
        ("Ghazieh", ["Ghaziyeh"]),
        ("Jezzine", []),
        ("Marjayoun", []),
        ("Nabatieh", ["Nabatiyeh"]),
        ("Naqoura", []),
        ("Saida", ["Sidon", "Saida Old Souks"]),
        ("Tyre", ["Sour", "Sur"]),
    ]),
    ("Bekaa", [
        ("Anjar", []),
        ("Baalbek", ["Baalbeck"]),
        ("Bar Elias", []),
        ("Chtaura", ["Chtoura", "Shtaura"]),
        ("Hermel", []),
        ("Joub Jannine", ["Jub Jannin"]),
        ("Machghara", []),
        ("Rayak", ["Riyaq"]),
        ("Taanayel", []),
        ("Zahle", ["Zahleh", "Zahlé"]),
    ]),
]

PLACE_NAMES = frozenset(name for _, places in REGIONS for name, _ in places)


def normalize(text):
    """Lower case, no accents, apostrophes and dashes as spaces: how a search
    is compared with a place, so "Zahlé", "zahle" and "ZAHLE" are the same."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("'", " ").replace("-", " ")
    return " ".join(text.split())


def search_terms(name, other_spellings=()):
    """Everything a place can be found by, as one string for the search box."""
    return " | ".join(dict.fromkeys(normalize(text) for text in (name, *other_spellings)))


def matches(query, name, other_spellings=()):
    """True when the query starts the place's name or a word in it, in any
    spelling: "ham" finds Hamra, "jdid" finds Tariq El Jdideh."""
    query = normalize(query)
    if not query:
        return True
    for text in (name, *other_spellings):
        text = normalize(text)
        if text.startswith(query) or f" {query}" in f" {text}":
            return True
    return False


def grouped_choices():
    """For a Django ChoiceField: [(region, [(name, name), ...]), ...]."""
    return [(region, [(name, name) for name, _ in places]) for region, places in REGIONS]


def spellings(name):
    for _, places in REGIONS:
        for place, others in places:
            if place == name:
                return others
    return []
