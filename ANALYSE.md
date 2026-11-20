# État des lieux technique

La référence [Eaux de Marseille](https://github.com/EnO33/eaux-marseille-ha) regroupe les fournisseurs marseillais autour d’un portail commun, configurable par interface. Son catalogue de fichiers sépare l’intégration de ses tests. Son README décrit un appareil par contrat, des capteurs, un sondage avec reprise, la réauthentification et l’import externe de statistiques. L’auteur documente aussi le recours au backend mobile sur les contrats qui ne remontent pas de données quotidiennes via le portail web. La configuration des tests et la [licence MIT](https://github.com/EnO33/eaux-marseille-ha/blob/main/LICENSE) ont été consultées. Le [client de connexion de Marseille](https://github.com/EnO33/eaux-marseille-ha/blob/main/custom_components/eaux_marseille/_auth.py) confirme que la connexion est précédée d’un échange sur `/webapi/Acces/generateToken`, que le jeton d’abonné arrive dans `tokenAuthentique` et qu’un identifiant de conversation accompagne les requêtes.

| Sujet | Réutilisation et adaptation pour Montpellier |
| --- | --- |
| Structure Home Assistant | Même découpage fonctionnel entre client, configuration, synchronisation, entités et tests. Une entrée et un appareil par contrat. Implémentation indépendante, sans reprise de code source de Marseille. |
| Authentification | Session HTTP privée avec cookies, contrôle des réponses, renouvellement sur refus de lecture et réauthentification par l’interface. Le script public de Montpellier confirme l’échange `generateToken` avant la connexion, les noms des champs et le jeton `tokenAuthentique`. Le client charge les paramètres de l’application depuis le script du portail. |
| Mise à jour | Premier chargement, puis une requête quotidienne et des reprises bornées, adaptées aux données journalières de Montpellier. |
| Mesures et Énergie | Le portail de Montpellier renvoie directement des index et des volumes datés. Les volumes historiques alimentent une source d’eau externe par contrat, avec les nouveaux champs de métadonnées du Recorder. |
| Tests | Fixtures indépendantes des identifiants, tests du protocole et des conversions, et tests ciblant Home Assistant Core 2026.11.2 pour le parcours de configuration et le Recorder. |

Pour Montpellier, la trace réseau confirme le POST d’obtention du jeton temporaire, le POST de connexion, les en-têtes et le GET des relevés. Le script public confirme le format des deux POST et identifie `isMFA` lorsque le compte exige un second facteur. La connexion depuis Home Assistant, les règles de pagination et la stabilité des horodatages des relevés corrigés restent à confirmer sur un compte réel. En cas de divergence, le traitement de la session est centralisé dans `api.py`.

Un historique publié avec retard exige d’importer des volumes à leur date de mesure, en conservant les cumuls ultérieurs. Les API du Recorder mettent à jour une heure existante lorsqu’un point portant le même identifiant et la même heure est réimporté. La version 2026.11 exige en plus les champs `mean_type` et `unit_class`. [Documentation du Recorder](https://contributors.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/)

Le dépôt est distinct de la référence et conserve son attribution. Licence du projet de référence © 2026 Contributors.
