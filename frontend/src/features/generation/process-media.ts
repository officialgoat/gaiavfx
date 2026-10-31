/** The asset listing's parameter that hides process files: a character replacement's intermediates, assembled shot
 * previews, replaced shots' versions and a stage's keyframe stills. Python owns which provenance steps those are
 * (application/process_media.py). The movie's reference bin always hides them (pilot T1-F2); the Library hides them
 * until "Show process files" is on (usability A2). */
export const HIDE_PROCESS_MEDIA = { hide_process: "true" };
