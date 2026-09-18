import time
import threading
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

VALGUSTUSE_VIITEAEG = 10 # Minutit enne ja pärast bronni läheb valgustus põlema/kustu
NEW_API_URL = "https://therand.ee/api/reservations"
NEW_API_KEY = "ikjaxh"
CONTROLLER_IP = "192.168.0.250"
TRIDONIC_AUTH_KEY = "wHNHcJk4iAghu6mBGaMSdVIEZ1I8/en79LV44mpu0pI="

VALJAKUTE_ID_TABEL = { 
    1: "94194d6a-6f65-4260-b2a8-d7492a637b2d",  #SendEvents esimene pikem kood ehk see mis 0: 
    12: "dd61267a-f16d-4f67-83b1-a88b07d261e1", 
    11: "cfc22ed0-e34c-4af6-9136-3085e8cf2010",
    2: "08cdcc21-7689-4933-99aa-1fabf0f382df",
    22: "e35da91e-9a58-4d1c-b7a6-ce782b0a5c19",
    23: "4b6c55c3-4c6b-44be-9a2a-cfd55d2120dc",
    3: "48858248-1f08-4b8d-a644-997295710f08",
    33: "ed6e2005-2138-4d9a-98aa-135606a8621e",
    34: "0c5b4baf-38ed-4e34-b974-a0be9af5a163",
    4: "3e941d49-ce4b-47dd-8b0c-a8b04ec26267",
    44: "982c7b7c-66ae-4232-90eb-96878577ec08", 
    5: "e2b6cc90-6c25-48d9-89ce-ea364dab258c", 
    55: "5df5aa07-9eac-45ae-8c9e-cf9769d7ff5c",
    56: "77c469e7-4f20-483a-8aea-bf00d9886314",
    6: "26aa39f8-2474-465d-9c7f-3260191171b3",
    66: "8aafc19d-9820-4e62-9210-7f63da0854d7"
}

UHISOSADE_POHIOSAD = {
    "soojendus": (11, 11),
    12: (11, 22),
    23: (22, 33),
    34: (33, 44),
    56: (55, 66),
}

# Alguses eeldame, et kõik on väljas
millised_polevad = {k: False for k in VALJAKUTE_ID_TABEL if k == "soojendus" or k > 10}

s_saali_ventikad_sees = False
v_saali_ventikad_sees = True
VENTIKATE_SEADISTUS = {
    "suur": {"ip": "192.168.0.251", "globaalne_muutuja": "s_saali_ventikad_sees"},
    "vaike": {"ip": "192.168.0.252", "globaalne_muutuja": "v_saali_ventikad_sees"}
}

bronnid = [] # Globaalne mälupuhver täna sisse laetud broneeringutest
viimane_kuupaev = None


def loe_uus_therand_reservations():
    """Loeb uue The Randi API kaudu reserveeringuandmed."""
    try:
        params = {"key": NEW_API_KEY}
        response = requests.get(NEW_API_URL, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        print("Ei saa uue The Randi API-ga ühendust. Ootan järgmist korda...")
        return None
    except requests.exceptions.HTTPError as exc:
        print(f"Uue The Randi API viga: {exc}")
        return None


def loeme_bronnid_jsonist(broneeringute_list):
    global bronnid, viimane_kuupaev
    tana = datetime.now(ZoneInfo('Europe/Tallinn')).date()

    # Kui päev vahetub, tühjendame kohaliku mälupuhvri
    if viimane_kuupaev != tana:
        bronnid = []
        viimane_kuupaev = tana

    if broneeringute_list is None:
        return bronnid

    def to_tallinn(value):
        if not value:
            return None
        value = value.strip()
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        if 'T' in value:
            try:
                dt = datetime.fromisoformat(value)
            except ValueError:
                dt = datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=ZoneInfo('UTC'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            return dt.astimezone(ZoneInfo('Europe/Tallinn'))
        return None

    # Eraldame API-st tulnud kehtivad tänased broneeringud
    aktiivsed_api_bronnid = []
    for andmed in broneeringute_list:
        court = andmed.get('court', {})
        nime_digits = ''.join(filter(str.isdigit, str(court.get('name') if isinstance(court, dict) else court)))
        if nime_digits:
            valjaku_nr = int(nime_digits)
        elif (court_id := andmed.get('court_id')) is not None:
            valjaku_nr = int(str(court_id)[-1])
        else:
            valjaku_nr = int(court) if isinstance(court, int) else 0

        algus_dt = to_tallinn(andmed.get('from_tz') or andmed.get('from'))
        lopp_dt = to_tallinn(andmed.get('to_tz') or andmed.get('to'))
        
        if not (algus_dt and lopp_dt):
            continue

        if algus_dt.date() == tana:
            kirje = (
                valjaku_nr,
                (algus_dt - timedelta(minutes=VALGUSTUSE_VIITEAEG)).strftime('%H:%M'),
                (lopp_dt + timedelta(minutes=VALGUSTUSE_VIITEAEG)).strftime('%H:%M')
            )
            aktiivsed_api_bronnid.append(kirje)

    # TUHISTAMISE KONTROLL (ainult tulevaste broneeringute puhul!)
    praegune_aeg = datetime.now().strftime('%H:%M')
    for eemaldatav in bronnid[:]:
        valjak, algus, lopp = eemaldatav[0], eemaldatav[1], eemaldatav[2]
        
        # Kui broneering EI OLE veel alkanud (algus > praegune_aeg) 
        # ja seda enam API vasteks pole, siis on see TÜHISTATUD.
        if algus > praegune_aeg and eemaldatav not in aktiivsed_api_bronnid:
            print(f"HOIATUS: Tulevikubroneering väljakule {valjak} ({algus}-{lopp}) tühistati ja eemaldati mälust.")
            bronnid.remove(eemaldatav)

    # Lisame uued tulnud broneeringud mälupuhvrisse
    for kirje in aktiivsed_api_bronnid:
        if kirje not in bronnid:
            bronnid.append(kirje)

    return bronnid


def lulita_ventilaatoreid(saal, olek):
    if saal not in VENTIKATE_SEADISTUS:
        print(f"Viga: Saali nimega '{saal}' ei ole seadistustes!")
        return

    ip = VENTIKATE_SEADISTUS[saal]["ip"]
    muutuja_nimi = VENTIKATE_SEADISTUS[saal]["globaalne_muutuja"]
    url = f"http://{ip}/relay/0?turn={olek}"

    try:
        response = requests.get(url, timeout=1.0)
        if response.status_code == 200:
            print(f"{saal.capitalize()} saali ventikad: {olek.upper()}")
            globals()[muutuja_nimi] = (olek == "on")
        else:
            print(f"Shelly viga ({saal} saal): Staatuse kood {response.status_code}")
    except requests.exceptions.RequestException:
        print(f"VIGA: Ei saanud ühendust {saal} saali Shellyga (IP: {ip}). Tahtsin panna olekusse: {olek.upper()}")


def kas_lulitada_ventikad():
    soovitud_olek_suur = any([millised_polevad.get(11), millised_polevad.get(22), 
                              millised_polevad.get(33), millised_polevad.get(44)])
    
    if soovitud_olek_suur and not s_saali_ventikad_sees:
        lulita_ventilaatoreid("suur", "on")
    elif not soovitud_olek_suur and s_saali_ventikad_sees:
        lulita_ventilaatoreid("suur", "off")


def lulita_valgustust(valjaku_nr):  
    if valjaku_nr not in VALJAKUTE_ID_TABEL:
        print(f"Viga: Väljakut nr {valjaku_nr} ei ole tabelis VALJAKUTE_ID_TABEL!")
        return

    target_zone = VALJAKUTE_ID_TABEL[valjaku_nr]
    url = f"http://{CONTROLLER_IP}/json/"

    params = {
        "action": "sendEvent",
        "event": "objectmodel.MethodCall",
        "arg[]": [target_zone, "toggleOnOff"],
    }

    try:
        response = requests.get(url, params=params, timeout=5) 
        if response.status_code == 200:
            print(f"Õnnestus! Väljak {valjaku_nr} (ID: {target_zone}) sai lülitatud.")
        elif response.status_code == 401:
            print(f"Kontrolleri viga: {response.status_code}. Kuid tegelikult lülitati edukalt väljak {valjaku_nr}.")
        else:
            print(f"Kontrolleri viga: {response.status_code}. Väljak {valjaku_nr} EI LÜLITUNUD.")

        if valjaku_nr != "soojendus" and valjaku_nr < 10:
            pohiosa = int(str(valjaku_nr) + str(valjaku_nr))
            if not millised_polevad[pohiosa]: 
                millised_polevad[pohiosa] = True

                uhisosaPluss = int(str(valjaku_nr) + str(valjaku_nr + 1))
                uhisosaMiinus = int(str(valjaku_nr - 1) + str(valjaku_nr))
                if uhisosaPluss in millised_polevad: 
                    millised_polevad[uhisosaPluss] = True  
                if uhisosaMiinus in millised_polevad:  
                    millised_polevad[uhisosaMiinus] = True 
                if valjaku_nr == 1:                  
                    millised_polevad["soojendus"] = True 
            else:
                print("SIIA EI TOHIKS ME JÕUDA!")
    except Exception as e:
        sekundid = 20
        print(f"Viga ühenduse loomisel (ilmselt asi netiühenduses):\n {e} \nPROOVIME UUESTI {sekundid}s pärast\n")
        time.sleep(sekundid)
        lulita_valgustust(valjaku_nr)


def kas_samale_valjakule_tuleb_vahetult_uus_bronn(bronnid_list, bronn, minutid, alg_v_lopp_nr, lopp_v_alg_nr):
    mone_teise_lopp_v_algus = (datetime.strptime(bronn[lopp_v_alg_nr], '%H:%M') + timedelta(minutes=minutid)).strftime('%H:%M')
    for teine_bronn in bronnid_list:
        if bronn[0] == teine_bronn[0] and mone_teise_lopp_v_algus == teine_bronn[alg_v_lopp_nr]:
            print(f"Praegu peaks lülitama Väljakut {bronn[0]}, aga kuna tuleb kohe UUS bronn peale, siis ei lülita valgustust kustu/põlema.")
            return True
    return False


def millised_panna_polema_kustu(bronnid_list):
    praegune_aeg = datetime.now().strftime('%H:%M')
    print(f"\n--- Kontrollin, kas midagi vaja lülitada. Kell on hetkel: {praegune_aeg} ---")

    polema_kustu = [[], []]

    # Prindime välja kõik tänased broneeringud, mille puhvriaeg on praeguseks möödas
    loppenud_bronnid = [b for b in bronnid_list if b[2] <= praegune_aeg]
    for b in loppenud_bronnid:
        print(f"Täna juba lõppenud bronn (koos puhvriga): {b}")

    for bronn in bronnid_list:
        valjak, algus, lopp = bronn[0], bronn[1], bronn[2]      
        pohiosa = int(str(valjak) + str(valjak)) 
        
        # Logime aktiivsed või tulekul olevad broneeringud
        if lopp > praegune_aeg:
            print(f"Tänane broneering mälus: Väljak {valjak}, Algus (puhvriga): {algus}, Lõpp (puhvriga): {lopp}") 

        # 1. SISSELÜLITAMISE KONTROLL
        if algus <= praegune_aeg < lopp and not millised_polevad.get(pohiosa):
            if not kas_samale_valjakule_tuleb_vahetult_uus_bronn(bronnid_list, bronn, +VALGUSTUSE_VIITEAEG * 2, 2, 1):
                polema_kustu[0].append(valjak)

        # 2. VÄLJALÜLITAMISE KONTROLL (Käivitub kui puhvri aeg `lopp` on käes või möödas)
        elif praegune_aeg >= lopp and millised_polevad.get(pohiosa):
            if not kas_samale_valjakule_tuleb_vahetult_uus_bronn(bronnid_list, bronn, -VALGUSTUSE_VIITEAEG * 2, 1, 2):
                polema_kustu[1].append(valjak)

    # Lülitame SISSE
    for valjak in set(polema_kustu[0]):
        print(f"Lülitan sisse väljak {valjak} (bronn algas praegu)")
        lulita_valgustust(valjak)

    # Lülitame VÄLJA ja eemaldame töödeldud bronnid
    tehtud_bronnid = []
    for valjak in set(polema_kustu[1]):
        print(f"Lülitan välja väljak {valjak} (bronn lõppes just praegu)")
        pohiosa = int(str(valjak) + str(valjak))
        lulita_valgustust(pohiosa) 
        millised_polevad[pohiosa] = False

            # Märgime selle broneeringu tehtuks, et seda enam ei kontrollitaks
        for bronn in bronnid_list:
            if bronn[0] == valjak and bronn[2] <= praegune_aeg:
                tehtud_bronnid.append(bronn)
    # Kustutame globaalsest mälust aegunud ja töödeldud broneeringud
    for b in tehtud_bronnid:
        if b in bronnid:
            bronnid.remove(b)


    # Lülitame välja ühisosad, mille mõlemad põhiosad on kustunud
    for uhisos, (pohiosa1, pohiosa2) in UHISOSADE_POHIOSAD.items():
        if not millised_polevad.get(pohiosa1, False) and not millised_polevad.get(pohiosa2, False):          
            if millised_polevad.get(uhisos, False):
                print(f"Lülitan välja ühise osa {uhisos}, sest põhiosad {pohiosa1} ja {pohiosa2} on kustunud")
                lulita_valgustust(uhisos)
                millised_polevad[uhisos] = False

    if not polema_kustu[0] and not polema_kustu[1]:
        print("Hetkel pole ühtegi väljakut, mida peaks sisse või välja lülitama. Ootan järgmist tsüklit...")


def kontrolli_kasutaja(stop_event):
    """Lõpetab peamise tsükli, kui kasutaja sisestab q/quit/exit."""
    print("Kirjuta 'q' ja vajuta ENTER, et programm lõpetaks ükskõik mis ajal.")
    while not stop_event.is_set():
        try:
            cmd = input()
        except EOFError:
            continue
        if cmd.strip().lower() in ("q", "quit", "exit"):
            print("Lõpetan tsükli...")
            stop_event.set()
            break


def oota_samalajal(stop_event, total_seconds):
    """Ootab koos stop_event kontrolliga, et tsükkel saaks kiiresti peatuda."""
    for _ in range(total_seconds):
        if stop_event.is_set():
            break
        time.sleep(1)


# --- PÕHITSÜKKEL ---
if __name__ == "__main__":
    stop_event = threading.Event()
    kontroll_thread = threading.Thread(target=kontrolli_kasutaja, args=(stop_event,), daemon=True)
    kontroll_thread.start()

    print("Programm käivitatud. Vaatan broneeringuid...")
    while not stop_event.is_set():
        andmed = loe_uus_therand_reservations() # Loeb uuendused API-st
        aktuaalsed_bronnid = loeme_bronnid_jsonist(andmed) # Lisab uued broneeringud kohalikku puhvrisse

        millised_panna_polema_kustu(aktuaalsed_bronnid)
        kas_lulitada_ventikad()

        if stop_event.is_set():
            break
        oota_samalajal(stop_event, 60) # Kontrollib iga 60 sekundi järel

    print("Programm lõpetatud.")