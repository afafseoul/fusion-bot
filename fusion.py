# Après "Connexion à Google Drive réussie"
try:
    # 1. Récupérer le dossier 'SOCIAL POSTING'
    main_folder_id = '1cXn22CJ8YlMftyARZcImJiMC4pSybOHE'

    # 2. Trouver le dossier du client
    response = drive_service.files().list(
        q=f"name='{client}' and mimeType='application/vnd.google-apps.folder' and '{main_folder_id}' in parents",
        spaces='drive',
        fields='files(id, name)'
    ).execute()
    folders = response.get('files', [])
    if not folders:
        return jsonify({"error": f"Dossier client '{client}' introuvable"}), 404
    client_folder_id = folders[0]['id']
    print(f"📁 Dossier du client trouvé : {client_folder_id}")

    # 3. Trouver la vidéo dans le dossier client (toutes sous-dossiers confondus)
    video_query = f"name='{video_name}' and '{client_folder_id}' in parents"
    video_response = drive_service.files().list(
        q=video_query,
        spaces='drive',
        fields='files(id, name)'
    ).execute()
    video_files = video_response.get('files', [])
    if not video_files:
        return jsonify({"error": f"Vidéo '{video_name}' introuvable avec la requête : {video_query}"}), 404
    video_id = video_files[0]['id']
    print(f"🎥 Vidéo trouvée : {video_name} (ID: {video_id})")

    # 4. Trouver le dossier Music
    music_folder_response = drive_service.files().list(
        q=f"name='Music' and '{client_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'",
        spaces='drive',
        fields='files(id, name)'
    ).execute()
    music_folders = music_folder_response.get('files', [])
    if not music_folders:
        return jsonify({"error": "❌ Aucun dossier 'Music' trouvé dans le dossier client"}), 404
    music_folder_id = music_folders[0]['id']
    print(f"📂 Dossier Music trouvé : {music_folder_id}")

    # 5. Sélectionner une musique
    music_files = drive_service.files().list(
        q=f"'{music_folder_id}' in parents and mimeType contains 'audio/'",
        spaces='drive',
        fields='files(id, name)'
    ).execute().get('files', [])
    if not music_files:
        return jsonify({"error": "❌ Aucun fichier audio trouvé dans le dossier Music"}), 404

    selected = random.choice(music_files)
    print(f"🎵 Musique sélectionnée : {selected['name']} (ID: {selected['id']})")

    return jsonify({"status": "success", "music": selected['name']}), 200

except Exception as err:
    print(f"🚨 ERREUR pendant le traitement : {err}")
    return jsonify({"error": str(err)}), 500
