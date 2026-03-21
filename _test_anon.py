"""Test de verbeterde anonymizer op de tekst uit het document."""
import sys
sys.path.insert(0, r'E:\scripts\webscraper\CBSbuurt\juridisch-assistent')

from anonymizer import Anonymizer

# Tekst uit het screenshot (AanvraagKortGeding_Botland_rekeningen.pdf)
test_text = """Aanvraag kort geding bij de kantonrechter Rechtbank Gelderland
met (concept-) dagvaarding in enkelvoud Locatie Arnhem
Administratie kort geding
Postbus 9030
6800 EM Arnhem
Zaaksoort Kantonzaak
Eisende partij Gedaagde partij
Naam Anne Spijker Octaaf Eugene Botland
Adres Laarfeldweg 2 Gubbelstraat 10C01
Emmerich Duitsland 6211CE Maastricht
Gemachtigde nvt
Kenmerk
Telefoon 0652635895 0654673538
Faxnummer
E-mailadres a.spijker@live.nl o.botland@live.nl
Verhinderdata beide 10, 12 juni 2024
partijen (6 weken)
Bijzondere verzoeken Verkorting dagvaardingstermijn: Ja
Verlenging zittingsduur : Nee"""

anon = Anonymizer()
result, mapping = anon.regex_only(test_text)

print("=" * 60)
print("MAPPING GEVONDEN:")
print("=" * 60)
for orig, repl in sorted(mapping.items(), key=lambda x: x[1]):
    print(f"  {repl:30s} <- {orig}")

print()
print("=" * 60)
print("GEVONDEN PII (voor database):")
print("=" * 60)
for orig, ptype, label in anon.found_pii:
    print(f"  [{ptype:12s}] {label:30s} <- {orig}")

print()
print("=" * 60)
print("GEANONIMISEERDE TEKST:")
print("=" * 60)
print(result)

print()
print(f"\nTotaal: {len(mapping)} vervangingen, {len(anon.found_pii)} PII items")
