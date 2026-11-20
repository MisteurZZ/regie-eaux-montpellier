# Régie des eaux Montpellier

Intégration personnalisée pour le portail abonnés de la Régie des eaux de Montpellier Méditerranée Métropole. Elle récupère les relevés du compteur d’eau et importe les volumes à leur date de mesure dans le tableau de bord Énergie. Elle est configurée depuis l’interface de Home Assistant et s’exécute sur Home Assistant OS sans navigateur.

**Version 0.1.0 pour essais.** Le traitement de l’historique est testé avec la réponse anonymisée de Montpellier. La requête de connexion exacte et la provenance des jetons doivent être confirmées sur un compte réel avant une utilisation régulière. La cible est Home Assistant OS avec Core 2026.11.x.

## Installation manuelle sur Home Assistant Green

1. Décompressez l’archive sur votre ordinateur. À l’aide de votre accès habituel à la configuration de Home Assistant, copiez le dossier `custom_components/regie_eaux_montpellier` dans le dossier `custom_components` de Home Assistant. Créez ce dernier s’il manque. Le fichier manifeste doit se trouver dans `/config/custom_components/regie_eaux_montpellier/manifest.json`.
2. Redémarrez Home Assistant.
3. Ouvrez **Paramètres → Appareils et services → Ajouter une intégration**, puis cherchez **Régie des eaux Montpellier**.
4. Saisissez votre identifiant, votre mot de passe et le numéro de contrat imprimé sur votre facture. Conservez les zéros initiaux. L’intégration valide l’ouverture de session et l’accès aux relevés avant de créer l’appareil.
5. Si vous avez plusieurs contrats, répétez l’ajout avec chaque numéro. Chaque contrat a son propre appareil, ses réglages et sa source Énergie. La saisie du numéro est manuelle.

## Installation depuis le Marketplace ou HACS

Les installations depuis un lien nécessitent une publication du code dans un dépôt GitHub public. Ce projet est livré avec les métadonnées nécessaires et le chemin prévu `MisteurZZ/regie-eaux-montpellier`. Si le dépôt est publié sous une autre URL, ajustez les champs `documentation`, `issue_tracker` et `codeowners` du manifeste. Cette version est livrée sous forme d’archive et de dépôt Git local.

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
- **Connexion inactive**. Les capteurs gardent les dernières valeurs connues. Après la première synchronisation, un échec temporaire décale les essais de 15, 30, 60 puis 120 minutes. Le portail peut demander un délai plus long avec `Retry-After`. En régime normal, la synchronisation a lieu au démarrage puis toutes les 24 heures.
- **Session expirée**. Un refus HTTP 401 ou 403 pendant la lecture déclenche une reconnexion et une seule reprise de la requête. Si la connexion elle-même refuse les identifiants, Home Assistant ouvre le parcours de réauthentification. Un refus de lecture répété peut aussi signaler un problème de contrat ou de protocole.
- **Réponse inattendue**. Vérifiez d’abord que vous pouvez vous connecter au portail. Consultez ensuite la liste de validation ci-dessous. Le client vérifie le format des mesures et signale une réponse paginée partielle plutôt que d’importer un historique tronqué à son insu.
- **Diagnostic**. Le fichier téléchargeable depuis l’intégration indique uniquement la réussite de la synchronisation et la présence de relevés et de volumes. Home Assistant stocke le mot de passe dans son entrée de configuration. Les cookies et jetons restent en mémoire pendant la session.

## Validation sur un contrat réel avant mise en production

1. Dans l’onglet Réseau du navigateur pendant votre connexion habituelle, vérifiez la méthode, le type de contenu et **les noms des champs** envoyés à `/webapi/Utilisateur/authentification`. Le client d’essai suppose un corps JSON avec `identifiant` et `motDePasse`.
2. Vérifiez si `Token` et `ConversationId` viennent des en-têtes de réponse, du JSON de connexion ou d’un autre échange normal du portail. Le client sait lire ces champs dans les en-têtes et à la racine du JSON de connexion. L’ordre d’émission des cookies et tout appel `generateToken` restent à confirmer. Pour documenter ces points, relevez uniquement les noms de champs, chemins et codes HTTP. Gardez les valeurs, mots de passe, jetons et cookies privés.
3. Vérifiez la forme d’une connexion refusée, le signal d’expiration et le renouvellement après une expiration réelle. Si le portail encode un refus dans un HTTP 200 ou impose un échange préalable, il faut adapter le client et sa fixture.
4. Vérifiez le nombre de résultats et la profondeur de l’historique sur un contrat plus ancien. Si `nbTotalResultats` dépasse le nombre de lignes retournées, il faut identifier les paramètres de pagination utilisés normalement par le portail. Vérifiez aussi le traitement des corrections et des éventuels intervalles couvrant plusieurs jours.
5. Comparez le dernier index et le dernier volume à l’agence en ligne. Dans Énergie, comparez plusieurs dates disponibles, puis relancez une synchronisation et redémarrez Home Assistant. Vérifiez que la source reste la même, que les jours gardent leur consommation et que le cumul ne double pas. Vérifiez le bouton d’actualisation et l’état de fraîcheur.

## Développement et licence

Les tests du client et de l’historique s’exécutent sans dépendance Home Assistant et sans identifiants.

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Pour exécuter aussi les tests du Config Flow, des entités et du Recorder, utilisez un environnement de test isolé avec Python 3.14.2 ou ultérieur.

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install 'homeassistant==2026.11.2' 'pytest-homeassistant==2026.11.2'
python -m pytest -q
```

Le projet est sous licence MIT. Le dépôt [Eaux de Marseille](https://github.com/EnO33/eaux-marseille-ha), d’EnO33 et de ses contributeurs, a servi de référence fonctionnelle. La [note d’analyse](ANALYSE.md) décrit les éléments consultés, les adaptations et les inconnues.
