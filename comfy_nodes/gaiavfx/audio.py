"""H3 audio VAE safety guard."""

import copy


class GaiaH3AudioVAE:
    @classmethod
    def INPUT_TYPES(cls):return {"required":{"audio_vae":("VAE",)}}
    RETURN_TYPES=("VAE",)
    FUNCTION="guard"
    CATEGORY="GAIAVFX/H3"

    def guard(self,audio_vae):
        if type(audio_vae.first_stage_model).__name__!="MiniMaxH3AudioVAE" or audio_vae.audio_sample_rate!=32000 or audio_vae.downscale_ratio!=800:
            raise ValueError("This guard accepts only the verified H3 Audio VAE contract.")
        safe=copy.copy(audio_vae)
        safe.crop_input=False
        return (safe,)

