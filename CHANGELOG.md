# Nouveautés

## 1.4.1 — 2 novembre 2026

**Corrections**
- Floraison : un écran du jeu qui ressemble au titre du menu Enrichir pouvait faire apparaître une erreur rouge sous les panneaux (« OpenCV … !_src.empty() in function 'cv::cvtColor' »). Le compteur ne prend plus pour le menu un titre trouvé ailleurs qu'à sa place.
- Une erreur de lecture de l'écran (Floraison ou cartes de capture) s'efface d'elle-même dès que la lecture remarche, au lieu de rester affichée jusqu'au redémarrage. Son détail est noté dans `data\erreurs.txt`.
- Après une installation à la main depuis la 1.2.0, l'ancien `Tester le compteur.bat`, qui ne marchait plus, est supprimé.

## 1.4.0 — 31 octobre 2026

**Nouveau nom : Aniitracker**
- Le Compteur pity Aniimo s'appelle maintenant **Aniitracker**. Le programme devient `Aniitracker.exe`, le zip `Aniitracker.zip`, et le projet est sur [github.com/Zanpil/aniitracker](https://github.com/Zanpil/aniitracker). Les anciens liens vers GitHub y mènent toujours. La page du projet, elle, change d'adresse : [zanpil.github.io/aniitracker](https://zanpil.github.io/aniitracker/).
- **Depuis la 1.3.0** : menu > *Installer la version 1.4.0…*, comme une mise à jour normale. Le programme prend son nouveau nom tout seul, et le lancement avec Windows suit.
- **Depuis une version plus ancienne** : mettez `Aniitracker.exe` dans le dossier de l'ancien compteur, celui qui contient `data\`. Vos compteurs et réglages sont repris, et l'ancien `Compteur pity Aniimo.exe` est supprimé au premier lancement.
- Si un raccourci vers l'ancien programme ne marche plus, refaites-le vers `Aniitracker.exe`.
- Rien d'autre ne change : mêmes compteurs, mêmes réglages, mêmes touches.

## 1.3.0 — 31 octobre 2026

**Mises à jour en un clic**
- Le compteur peut chercher lui-même les nouvelles versions sur GitHub. Il vous le demande au premier lancement, et ça se change dans les réglages. Quand une version sort, la bulle porte un **point doré** et le menu propose *Installer la version…*. Le compteur la télécharge, vérifie qu'elle n'est pas abîmée, remplace le programme et redémarre. Vos compteurs, vos réglages et la place des panneaux sont gardés : plus besoin de supprimer le dossier.
- Ça marche à partir de cette version : la 1.3.0 s'installe encore à la main, en remplaçant l'ancien `Compteur pity Aniimo.exe` par le nouveau.

**Lancement avec Windows**
- Réglages > *Lancer le compteur avec Windows* : le compteur démarre en arrière-plan avec Windows et attend Aniimo. Ses panneaux apparaissent quand le jeu se lance et disparaissent quand il se ferme.
- Lancé une deuxième fois, le compteur ne dit plus « tourne déjà » : celui qui tourne affiche ses panneaux et ouvre son menu.

**Floraison**
- La barre du panneau suit maintenant **celle du jeu**. Elle montre l'avancée dans l'état de la zone (« Abondant 81 % »), comme le menu Enrichir. Avant, elle montrait le total sur 10 500 et ne ressemblait pas à la barre du jeu. Le total et le reste à remplir sont toujours affichés.
- Lecture plus stable : le compteur attend que la barre du jeu s'arrête (elle se remplit à l'ouverture du menu et quand on change de zone) et ignore les petits écarts de mesure. Le panneau ne clignote plus et ne fait plus de va-et-vient.
- Les noms de zone sont mieux reconnus, même sur deux lignes ou sous l'étiquette « Augmenter ». Une zone non reconnue n'est plus enregistrée en « Zone ? ».

**Fiabilité**
- Une erreur imprévue ne peut plus arrêter le comptage. Elle s'affiche sous les panneaux, elle est notée dans `data\erreurs.txt`, et le compteur continue.
- Si l'historique des captures est bloqué un instant (antivirus, dossier synchronisé avec OneDrive), le compteur réessaie. Il garde les captures en mémoire et les enregistre dès que c'est possible.
- Menu > *Vérifier le fonctionnement* remplace `Tester le compteur.bat`. Il vérifie le son du jeu, l'écran et les fichiers, et donne les dernières erreurs notées.

**Menu plus facile**
- Nouvelle **bulle** flottante (le logo) : un clic ouvre le menu. On la déplace et la redimensionne comme les panneaux, et on peut la cacher dans les réglages.
- Le menu est maintenant une petite fenêtre qui **reste ouverte** jusqu'à ce qu'on la ferme : ✕, Échap, la bulle à nouveau ou une action. Le clic droit sur un panneau ouvre le même menu.
- Correction : le menu du clic droit se refermait tout seul, parce que le compteur repassait ses panneaux au premier plan par-dessus lui toutes les 2 secondes. Le menu et les fenêtres de réglage restent maintenant au-dessus des panneaux.

**Panneaux**
- Chaque panneau a sa **propre taille** dans les réglages (Lucky, Super Lucky, Floraison, bulle). Ctrl + molette sur un panneau marche toujours.
- Chaque panneau se **verrouille** séparément : menu > *Panneaux* > *verrouillé*. Un panneau verrouillé ne bouge plus.
- Des panneaux peuvent être **liés** : menu > *Panneaux* > *lié*. Déplacer un panneau lié déplace tous les panneaux liés. *Tout verrouiller* bloque tout d'un coup.
- Le menu montre aussi les zones Floraison lues, avec les Lucky, Super Lucky et Floraisons restants de chacune.

## 1.2.0 — 31 octobre 2026

**Panneaux séparés**
- Lucky, Super Lucky et Floraison sont maintenant trois panneaux indépendants : placez chacun où vous voulez.
- Glisser un panneau le déplace seul. **Maj + glisser** déplace tous les panneaux ensemble.
- Un panneau approché d'un autre s'aimante : en dessous, à côté ou aligné. Il s'aimante aussi aux bords de l'écran.
- Vos positions précédentes sont reprises.

**Taille réglable**
- **Ctrl + molette** sur un panneau l'agrandit ou le rétrécit, de 60 % à 160 %.
- Clic droit > *Réglages…* > *Taille* règle tous les panneaux d'un coup.

**Floraison**
- L'encadré « Restant » donne aussi l'équivalent en **Floraisons** (enrichissements), en plus des Lucky et des Super Lucky. Il compte environ 480 points par enrichissement, réglable avec `"floraison_gain"` dans `config.json` (720 pendant les événements d'abondance de la Veine).
- Le menu du panneau Floraison (clic droit) donne la fourchette de Lucky selon la chance, et les Floraisons de chaque zone lue.

**Réglages**
- Les trois panneaux s'affichent ou se cachent séparément. Un au moins reste affiché, pour garder le clic droit vers les réglages.

## 1.1.0 — 31 octobre 2026

- Panneaux arrondis bleu nuit, avec un médaillon : étoile dorée pour le Lucky, étoile rose pour le Super Lucky, cristal pour la Floraison.
- Grands chiffres « 114 / 200 (57 %) », barre en dégradé, et une carte « cette semaine » sur la droite.
- Sous le titre : dans combien de captures le coup est garanti, et le nombre de captures de la semaine.
- Autour des panneaux, la fenêtre est transparente : on voit le jeu entre eux.
- Nouvelle icône.

## 1.0.0 — 31 octobre 2026

Première version publique.
- Captures comptées automatiquement : cartes de capture à l'écran, captures de groupe et Culbuton compris.
- Lucky et Super Lucky reconnus au son du jeu seul.
- Garantie (pity) Lucky et Super Lucky, avec les règles tirées des fichiers du jeu.
- Bandeau Floraison : énergie prismatique d'une zone lue dans le menu Enrichir, points manquants, Lucky et Super Lucky nécessaires.
- Réglages : éléments affichés, touches de correction, serveur.
