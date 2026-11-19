# SystemPulse

SystemPulse registra una cronologia locale per ricostruire un rallentamento dopo che è avvenuto. L’app desktop mostra CPU, memoria e processi attorno a un episodio segnato dall’utente. È disponibile anche un comando da terminale.

## Avvio su Windows

### App desktop

1. Scarica `SystemPulse-Setup-Windows-x64.exe` dalle [Releases di GitHub](https://github.com/vxorn/systempulse/releases/latest), esegui l’installer e apri **SystemPulse** dal menu Start.
2. Premi **Avvia raccolta**. Per acquisire i campioni precedenti a un rallentamento, lascia aperta l’app mentre lavori.
3. Appena noti un rallentamento, premi **Segna rallentamento**. La timeline passa all’episodio e si aggiorna con i campioni successivi. Seleziona un punto nei grafici o una riga della tabella per vedere i processi osservati in quel campione. In **Cronologia** puoi tornare agli altri episodi.

L’app si installa per l’utente corrente su Windows x64 e non richiede un’installazione separata di Python. L’installer è privo di firma digitale. La raccolta termina alla chiusura dell’app. La disinstallazione rimuove l’app e il collegamento dal menu Start. Lo storico resta in `%LOCALAPPDATA%\SystemPulse`.

### Pacchetto portatile da terminale

Scarica `SystemPulse-Windows-x64.zip` dalle [Releases di GitHub](https://github.com/vxorn/systempulse/releases/latest) ed estrai l’intera cartella. Il pacchetto include già Python e le dipendenze. Da PowerShell, dentro la cartella estratta:

```powershell
.\SystemPulse.exe capture
```

In un secondo terminale, dalla stessa cartella:

```powershell
.\SystemPulse.exe mark "Blocco durante il lavoro"
.\SystemPulse.exe timeline --latest
```

Attendi qualche secondo fra il marker e la lettura per avere campioni successivi al marker. Il pacchetto è per Windows x64 e l’eseguibile è privo di firma digitale.

### Installazione dai sorgenti

Installa Python 3.11 o successivo. Da PowerShell, nella radice del progetto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\systempulse.exe capture
```

Lascia il collector attivo. In un secondo PowerShell, dalla stessa cartella, marca l’episodio appena lo noti:

```powershell
.\.venv\Scripts\systempulse.exe mark "Blocco durante il lavoro"
.\.venv\Scripts\systempulse.exe markers
```

Attendi 15 secondi per raccogliere i campioni successivi e apri l’ultimo episodio:

```powershell
.\.venv\Scripts\systempulse.exe timeline --latest
```

Usa `timeline --marker ID` per aprire un episodio precedente. Senza `--latest` o `--marker`, `timeline` mostra gli ultimi secondi rispetto all’ora corrente. Per usare un archivio alternativo, inserisci `--db percorso.sqlite3` prima del sottocomando in tutti i terminali. Usa un solo collector per archivio, anche quando usi sia l’app desktop sia il terminale. Ferma il collector con Ctrl+C.

## Architettura

| File | Responsabilità |
| --- | --- |
| `systempulse/collector.py` | Rileva CPU di sistema, RAM e processi con psutil. Usa l’orologio monotono per gli intervalli e UTC per collocare campioni e marker. |
| `systempulse/storage.py` | Salva campione e processi nella stessa transazione SQLite. La modalità WAL consente la lettura durante la raccolta e SQLite serializza le scritture del collector e dei marker. |
| `systempulse/cli.py` | Avvia la raccolta, registra marker e mostra gli offset prima e dopo l’evento, la copertura dei processi e le interruzioni tra campioni. |
| `tests/` | Verifica misurazione, riuso dei PID, conservazione, atomicità, comandi e acquisizione reale. |

`psutil` è l’unica dipendenza di runtime. SQLite, CLI e test usano la libreria standard Python. I criteri di accettazione sono la raccolta di campioni reali, un marker scritto durante la cattura, la lettura dei due lati dell’evento e la scadenza della cronologia.

## Come leggere i dati

- La frequenza obiettivo è un campione ogni 2 secondi. Il primo passaggio stabilisce la baseline della CPU. La colonna intervallo indica il tempo monotono effettivo tra le letture della CPU. Un distacco superiore a 3 secondi tra timestamp è segnalato in vista. Timestamp e offset risentono delle correzioni dell’orologio di sistema.
- La CPU di sistema è mediata sull’intervallo. La RAM mostrata è il totale meno quella disponibile. Per processo, il 100% equivale all’uso pieno di un core logico durante l’intervallo. RSS è la memoria residente, che può includere pagine condivise. Il collector scansiona i processi e conserva al massimo i 5 con maggiore CPU osservata, usando l’RSS per sciogliere le parità. Processi nuovi, terminati o inaccessibili possono mancare dalla classifica. La copertura mostra quanti processi enumerati avevano una misura utilizzabile.
- Un marker registra l’ora del comando e usa lo storico già raccolto. I marker seguono la stessa conservazione dei campioni. Un picco più breve dell’intervallo può essere attenuato o sfuggire. Il costo della scansione dipende dal numero di processi e va misurato su Windows.

## Archivio e privacy

L’archivio predefinito è `%LOCALAPPDATA%\SystemPulse\timeline.sqlite3` su Windows e `~/.systempulse/timeline.sqlite3` in assenza di `LOCALAPPDATA`. Conserva fino a 24 ore di dati visibili, con un massimo di 43.200 campioni e 1.000 marker. Le scritture eliminano le righe scadute e SQLite riusa lo spazio liberato. Il file può mantenere la dimensione raggiunta e la scadenza logica non equivale a cancellazione sicura.

Il database contiene nomi e PID dei processi, metriche e note dei marker. Evita dati sensibili nelle note. Il programma opera in locale e non invia telemetria.

Per avviare l’app dai sorgenti dopo l’installazione Python, esegui `.\.venv\Scripts\systempulse-desktop.exe`.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

La prova d’integrazione avvia il collector reale, usa un archivio temporaneo e controlla i campioni ai due lati di un marker. Il data set di produzione nasce esclusivamente dal collector.

Il workflow GitHub Actions `Windows tests` installa il pacchetto ed esegue la stessa suite su Windows con Python 3.11 e 3.14 a ogni push su `main` e a ogni pull request. Compila anche lo ZIP portatile e verifica la raccolta dall’eseguibile estratto. Un job separato installa, avvia e disinstalla l’app desktop.
