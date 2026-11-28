# Régie des eaux Montpellier

Intégration personnalisée pour le portail abonnés de la Régie des eaux de Montpellier Méditerranée Métropole. Elle récupère les relevés du compteur d’eau et importe les volumes à leur date de mesure dans le tableau de bord Énergie. Elle est configurée depuis l’interface de Home Assistant et s’exécute sur Home Assistant OS sans navigateur.

**Version 0.1.7.** L’intégration récupère les relevés chaque jour à 05 h 15, heure de Paris. Elle permet d’ajouter le coût estimé de l’eau au tableau de bord Énergie. La connexion et la récupération des relevés ont été vérifiées sur un compte réel avec une version précédente sur Home Assistant Green et Core 2026.11.x.

## Installation manuelle sur Home Assistant Green

1. Décompressez l’archive sur votre ordinateur. À l’aide de votre accès habituel à la configuration de Home Assistant, copiez le dossier `custom_components/regie_eaux_montpellier` dans le dossier `custom_components` de Home Assistant. Créez ce dernier s’il manque. Le fichier manifeste doit se trouver dans `/config/custom_components/regie_eaux_montpellier/manifest.json`.
2. Redémarrez Home Assistant.
3. Ouvrez **Paramètres → Appareils et services → Ajouter une intégration**, puis cherchez **Régie des eaux Montpellier**.
4. Saisissez votre identifiant et votre mot de passe. Laissez le numéro de contrat vide pour utiliser le contrat par défaut de votre compte, comme l’agence en ligne à l’ouverture, ou saisissez celui imprimé sur votre facture en conservant les zéros initiaux. L’intégration valide l’ouverture de session et l’accès aux relevés avant de créer l’appareil.
5. Si vous avez plusieurs contrats, répétez l’ajout avec chaque numéro. Chaque contrat a son propre appareil, ses réglages et sa source Énergie. Pour un contrat autre que celui par défaut, saisissez son numéro.

## Installation depuis le Marketplace ou HACS

Les installations depuis un lien nécessitent une publication du code dans un dépôt GitHub public. Ce projet est livré avec les métadonnées nécessaires et le chemin prévu `MisteurZZ/regie-eaux-montpellier`. Si le dépôt est publié sous une autre URL, ajustez les champs `documentation`, `issue_tracker` et `codeowners` du manifeste. Le dépôt GitHub `MisteurZZ/regie-eaux-montpellier` contient le code de l’intégration.

- **Home Assistant 2026.11.x**. HACS est remplacé par le Marketplace intégré. Après publication, ouvrez **Paramètres → Marketplace → Ajouter depuis un lien**, connectez GitHub si demandé, entrez l’URL publique et installez le projet comme intégration. Redémarrez Home Assistant si l’interface le demande. [Documentation du Marketplace](https://www.home-assistant.io/integrations/marketplace/)
- **Home Assistant 2026.10 avec HACS**. Dans **HACS → menu ⋮ → Dépôts personnalisés**, entrez l’URL publique, sélectionnez **Intégration**, puis téléchargez l’intégration et redémarrez. Cette version de Home Assistant est compatible avec les API visées et le parcours HACS reste à vérifier sur une instance. [Documentation HACS](https://www.hacs.xyz/docs/faq/custom_repositories/)

Après installation, ajoutez le contrat dans **Paramètres → Appareils et services**.

## Entités

| Entité | Valeur |
| --- | --- |
| Index du compteur | Index cumulatif affiché à trois décimales en m³. L’index de l’API est en litres. |
| Consommation du dernier relevé | Volume publié pour l’intervalle qui se termine à la date du dernier relevé, en L. Si le volume manque, le capteur est inconnu. |
| Date du dernier relevé | Date et heure réelles du dernier index. |
| Moyenne quotidienne | Volume moyen en L par date de mesure comportant un volume, sur l’historique disponible. Un premier intervalle de 18 h compte pour sa valeur publiée, sans extrapolation. |
| Consommation du mois | Somme des volumes publiés pour le mois courant à Paris, en m³. |
| Consommation de l’année | Somme des volumes publiés pour l’année courante à Paris, en m³. |
| Connexion au portail | Résultat de la dernière synchronisation, heure du dernier relevé et âge. L’attribut `reading_stale` passe à vrai au-delà de 72 h. |
| Consommation élevée | Passe à « détecté » si la consommation du dernier intervalle dépasse le seuil défini dans les options de l’intégration. Par défaut, le seuil à 0 désactive l’alerte. |
| Actualiser les relevés | Bouton de synchronisation manuelle soumis au même délai de reprise et à un intervalle minimal de 60 s. |

Pour recevoir une notification lorsque le seuil est franchi, utilisez le capteur **Consommation élevée** comme déclencheur d’une automatisation. Pour évaluer chaque nouveau relevé, déclenchez sur **Date du dernier relevé** et vérifiez le volume en condition. L’alerte suit la fréquence de publication de la Régie et porte sur des volumes relevés, parfois avec jusqu’à 72 h de retard.

## Tableau de bord Énergie

1. Attendez la réussite de la première synchronisation avec au moins un relevé portant un volume.
2. Dans **Outils de développement → Statistiques**, recherchez **Régie des eaux Montpellier · Eau**. Le suffixe permet de distinguer les contrats. L’identifiant de la source est également affiché dans les attributs de **Connexion au portail**.
3. Ouvrez **Paramètres → Tableaux de bord → Énergie**. Dans **Source d’eau → Ajouter une consommation**, choisissez cette statistique externe. Utilisez une seule source par compteur physique. Le capteur **Index du compteur** sert à vérifier le relevé affiché et sa configuration ne génère pas une seconde statistique de consommation.
4. Affichez une période couverte par le portail. Le jour de mesure s’affiche correctement lorsque le fuseau horaire de Home Assistant est réglé sur **Europe/Paris**.

Le Recorder reçoit des cumuls absolus en m³, indexés par heure de mesure en UTC. Un point de référence au début de l’heure précédente permet à Home Assistant de calculer la différence du premier volume. Ce point porte uniquement le cumul déjà connu. L’heure de mesure sert à dater le volume total de l’intervalle. Les volumes sont regroupés par date locale pour les capteurs mensuels et annuels. Les deux occurrences d’une même heure lors du passage à l’heure d’hiver restent distinctes.

Les relevés reçus tardivement reviennent à leur date de mesure. Les nouveaux volumes et les corrections explicites à un horodatage existant recalculent les cumuls affectés. Une nouvelle importation remplace les points existants à ces heures, ce qui évite les doublons. L’historique est conservé dans le stockage interne de l’intégration à travers les redémarrages et les réponses incomplètes. Un jour manquant reste sans relevé et sans estimation. Si une ligne déjà connue perd son champ volume dans une réponse suivante, le dernier volume publié est conservé. Une correction qui change l’horodatage d’une ligne exige un identifiant de relevé stable ou une règle de remplacement à vérifier auprès du portail.

### Coût estimé de l’eau

1. Dans **Paramètres → Appareils et services → Régie des eaux Montpellier → Configurer**, activez **Coût estimé** pour votre contrat. Le tarif proposé est **3,6078 €/m³ TTC**. Choisissez la date de départ de l’estimation. Par défaut, le formulaire propose la date du plus ancien relevé qui comporte un volume.
2. Dans la configuration du tableau de bord **Énergie**, ouvrez votre source d’eau existante puis sélectionnez **Utiliser une entité de suivi des coûts totaux**. Choisissez **Régie des eaux Montpellier · Coût estimé**, avec le même suffixe que votre source d’eau. La source de coût apparaît dès qu’un relevé porte un volume à partir de la date de départ choisie.
3. Pour modifier le prix, saisissez le nouveau tarif et sa date d’entrée en vigueur dans les options. Choisissez une date future, au plus tôt le lendemain en heure de Paris. Un relevé publié en retard est valorisé au tarif de sa date de mesure. Une correction de volume recalcule les cumuls au tarif applicable à cette même date.

Le tarif par défaut est une moyenne issue de la facture du 20 octobre 2026 (230,90 € TTC de consommation pour 64 m³). Il couvre l’eau potable, l’assainissement et les taxes. L’abonnement fixe de 9,50 € TTC par semestre est exclu. Le tarif marginal peut varier selon les tranches de facturation. Avec le tarif moyen, les 1,372 m³ de l’exemple ci-dessous correspondent à **4,95 €** arrondis au centime.

La désactivation suspend les nouveaux imports de coûts. Les statistiques déjà enregistrées et les tarifs sont conservés. Retirez la source de coût du tableau de bord si vous souhaitez masquer les montants. La réactivation reprend l’historique disponible avec les dates et tarifs enregistrés.

### Résultat de référence

| Date de mesure | Index | Volume |
| --- | ---: | ---: |
| 16 novembre 2026 à 18 h | 483,466 m³ | Absent |
| 17 novembre 2026 à 12 h | 483,895 m³ | 429 L |
| 18 novembre 2026 à 12 h | 484,305 m³ | 410 L |
| 19 novembre 2026 à 12 h | 484,838 m³ | 533 L |

Le cumul des trois intervalles est de **1,372 m³**. La moyenne des dates ayant un volume est de **457,3 L**. Le 16 novembre fournit l’index de départ.

## Synchronisation et dépannage

- **Données anciennes avec connexion active**. Comparez la date du dernier relevé au portail. L’intégration relit la liste disponible à chaque synchronisation et permet ainsi de récupérer les journées publiées en retard.
- **Connexion inactive**. Les capteurs gardent les dernières valeurs connues. Après la première synchronisation, un échec temporaire décale les essais de 15, 30, 60 puis 120 minutes. Le portail peut demander un délai plus long avec `Retry-After`. En régime normal, la synchronisation a lieu au démarrage puis chaque jour à 05 h 15 en heure de Paris, avec prise en compte des changements d’heure.
- **Session expirée**. Un refus HTTP 401, 403 ou 410 pendant la lecture déclenche une reconnexion et une seule reprise de la requête. Si la connexion elle-même refuse les identifiants, Home Assistant ouvre le parcours de réauthentification. Le journal indique les codes HTTP successifs si le portail rejette aussi la session renouvelée.
- **Réponse inattendue**. Vérifiez d’abord que vous pouvez vous connecter au portail. Dans **Paramètres → Système → Journaux**, recherchez `Portal login validation failed` ou `Portal readings validation failed` pour distinguer l’étape en cause. En cas de refus HTTP, le journal identifie la page de connexion, le script public, le jeton anonyme ou la requête de connexion. Consultez ensuite la liste de validation ci-dessous. Le client vérifie le format des mesures et signale une réponse paginée partielle plutôt que d’importer un historique tronqué à son insu.
- **Diagnostic**. Le fichier téléchargeable depuis l’intégration indique uniquement la réussite de la synchronisation et la présence de relevés et de volumes. Home Assistant stocke le mot de passe dans son entrée de configuration. Les cookies et jetons restent en mémoire pendant la session.

## Validation sur un contrat réel avant mise en production

Le client lit les paramètres de l’application publiés dans `/app/app.js` sur le portail. Il les utilise pour obtenir un jeton temporaire auprès de `/webapi/Acces/generateToken`, puis transmet ce jeton au POST `/webapi/Utilisateur/authentification`. La réponse de connexion renvoie le jeton de session dans `tokenAuthentique`. L’intégration conserve les paramètres publics, jetons et cookies en mémoire. Si le compte exige une seconde étape d’authentification, l’intégration affiche un message dans le formulaire.

1. Vérifiez que Home Assistant charge les relevés de votre contrat après la saisie des identifiants. Si le formulaire affiche une erreur, relevez uniquement le message affiché et les lignes de journal commençant par `Portal login validation failed` ou `Portal readings validation failed`.
2. Comparez le dernier index et le dernier volume à l’agence en ligne, puis vérifiez le bouton d’actualisation. Après une expiration réelle, vérifiez que la synchronisation suivante renouvelle la session.
3. Dans Énergie, comparez plusieurs dates disponibles. Relancez une synchronisation et redémarrez Home Assistant. Vérifiez que la source reste la même, que les jours gardent leur consommation et que le cumul reste exact.
4. Si le coût estimé est activé, comparez le montant affiché dans Énergie au volume couvert multiplié par le tarif en vigueur aux dates de mesure. Choisissez une période couverte par un seul tarif pour le premier contrôle.
5. Sur un contrat plus ancien, vérifiez le nombre de résultats et la profondeur de l’historique. Si `nbTotalResultats` dépasse le nombre de lignes retournées, identifiez les paramètres de pagination utilisés par le portail. Vérifiez aussi le traitement des corrections et des intervalles couvrant plusieurs jours.

## Développement et licence

Les tests du client, de l’historique et des tarifs s’exécutent sans dépendance Home Assistant et sans identifiants.

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Pour tester la connexion à un compte de la Régie depuis un Mac sans réinstaller Home Assistant, lancez `python3 -m tools.check_portal` depuis la racine du dépôt avec Python 3.9 ou ultérieur. Le terminal masque la saisie des identifiants et du contrat. Le résultat affiche le nombre de relevés ou le détail non sensible de l’erreur. Ce contrôle utilise le même client et le transport HTTPS de Python.

Pour exécuter aussi les tests du Config Flow, des entités et du Recorder, utilisez un environnement de test isolé avec Python 3.14.2 ou ultérieur.

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install 'homeassistant==2026.11.2' 'pytest-homeassistant==2026.11.2'
python -m pytest -q
```

Le projet est sous licence MIT. Le dépôt [Eaux de Marseille](https://github.com/EnO33/eaux-marseille-ha), d’EnO33 et de ses contributeurs, a servi de référence fonctionnelle. La [note d’analyse](ANALYSE.md) décrit les éléments consultés, les adaptations et les inconnues.
