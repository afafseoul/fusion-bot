from flask import Flask, request, jsonify
import os, uuid, base64, json, tempfile, traceback, re
from video_generator import generate_video

app = Flask(__name__)

@app.get("/")
def health():
    return "🟢 fusion-bot ready"


# ----------------- helpers -----------------

def _normalize_plan(raw_plan):
    """
    Accepte:
      - list (déjà un plan)
      - dict (prend 'plan' si présent, sinon dict->list invalide)
      - str JSON (avec ou sans clé 'plan')
    Retourne une LISTE d'items.
    """
    if raw_plan is None:
        raise ValueError("Missing 'plan'")

    # Déjà une liste ?
    if isinstance(raw_plan, list):
        return raw_plan

    # Dict : peut contenir 'plan'
    if isinstance(raw_plan, dict):
        candidate = raw_plan.get("plan", raw_plan)
        if isinstance(candidate, list):
            return candidate
        raise ValueError("Object provided for 'plan' is not a list")

    # String JSON
    if isinstance(raw_plan, str):
        obj = json.loads(raw_plan)
        if isinstance(obj, dict) and "plan" in obj:
            obj = obj["plan"]
        if isinstance(obj, list):
            return obj
        raise ValueError("String JSON for 'plan' did not resolve to a list")

    raise ValueError(f"Unsupported 'plan' type: {type(raw_plan).__name__}")


def _decode_audio(audio_value):
    """
    Accepte:
      - base64 (avec ou sans préfixe 'data:...;base64,')
      - bytes (ou string binaire accidentelle)
    Retourne des bytes (le contenu audio).
    """
    if audio_value is None:
        raise ValueError("Missing 'audio_base64'")

    # anciennes structures éventuelles { "data": ... }
    if isinstance(audio_value, dict) and "data" in audio_value:
        audio_value = audio_value["data"]

    # retire 'data:...;base64,' s'il est présent
    if isinstance(audio_value, str) and audio_value.startswith("data:"):
        audio_value = re.sub(r"^data:.*?;base64,", "", audio_value)

    # tente le décodage base64 strict
    if isinstance(audio_value, str):
        try:
            return base64.b64decode(audio_value, validate=True)
        except Exception:
            # si ce n'est pas un base64 valide -> on traite comme binaire dans une string
            return audio_value.encode("latin1", "ignore")

    # bytes-like
    try:
        return bytes(audio_value)
    except Exception:
        raise ValueError("Invalid audio payload (not base64 nor bytes)")


# ----------------- route -----------------

@app.post("/create-video")
def create_video():
    # Parse JSON
    try:
        data = request.get_json(force=True, silent=False) or {}
    except Exception as e:
        app.logger.exception("Invalid JSON body")
        return jsonify({"error": f"Invalid JSON body: {e}"}), 400

    try:
        # ---- paramètres vidéo ----
        output_name = data.get("output_name", f"{uuid.uuid4().hex}.mp4")
        width  = int(data.get("width", 1080))
        height = int(data.get("height", 1920))
        fps    = int(data.get("fps", 30))

        # ---- plan ----
        try:
            plan = _normalize_plan(data.get("plan"))
            if not plan:
                return jsonify({"error": "'plan' is empty"}), 400
        except Exception as e:
            app.logger.exception("Plan normalization failed")
            return jsonify({"error": f"Invalid 'plan': {e}"}), 400

        # ---- audio (clé tolérante) ----
        audio_in = data.get("audio_base64") or data.get("audio_bas e64") or data.get("audio_b64")
        try:
            audio_bytes = _decode_audio(audio_in)
        except Exception as e:
            app.logger.exception("Audio decode failed")
            return jsonify({"error": f"Invalid 'audio_base64': {e}"}), 400

        # ---- fichiers de travail ----
        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        audio_path = os.path.join(workdir, "audio.mp3")
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        # ---- génération vidéo ----
        output_path = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width,
            height=height,
            fps=fps,
        )

        return jsonify({"status": "success", "output_path": output_path}), 200

    except Exception as e:
        # log stacktrace complet dans Render
        traceback.print_exc()
        app.logger.exception("Unhandled /create-video error")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # en local seulement; sur Render c'est gunicorn qui lance
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
