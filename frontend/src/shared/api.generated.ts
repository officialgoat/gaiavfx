/** Generated from the Python API. Run npm run generate:contracts. */
export interface paths {
  "/api/v1/session": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Session */
    post: operations["session_api_v1_session_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/health": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Health */
    get: operations["health_api_v1_health_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/library": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Library */
    get: operations["get_library_api_v1_library_get"];
    put?: never;
    /** Create Library */
    post: operations["create_library_api_v1_library_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/imports": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Import Media */
    post: operations["import_media_api_v1_imports_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Assets */
    get: operations["assets_api_v1_assets_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets/{asset_id}/urls": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Media Urls */
    get: operations["media_urls_api_v1_assets__asset_id__urls_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets/{asset_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Asset */
    get: operations["asset_api_v1_assets__asset_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets/{asset_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Review Asset */
    patch: operations["review_asset_api_v1_assets__asset_id__review_patch"];
    trace?: never;
  };
  "/api/v1/collections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Collections */
    get: operations["collections_api_v1_collections_get"];
    put?: never;
    /** Create Collection */
    post: operations["create_collection_api_v1_collections_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/collections/{collection_id}/assets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Assign Collection */
    post: operations["assign_collection_api_v1_collections__collection_id__assets_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/entities": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Entities */
    get: operations["entities_api_v1_entities_get"];
    put?: never;
    /** Create Entity */
    post: operations["create_entity_api_v1_entities_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/entities/{entity_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Update Entity */
    put: operations["update_entity_api_v1_entities__entity_id__put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/entities/{entity_id}/assets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Assign Entity */
    post: operations["assign_entity_api_v1_entities__entity_id__assets_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/media/{asset_id}/{rendition}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Media File */
    get: operations["media_file_api_v1_media__asset_id___rendition__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/assistant/credentials": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Credentials */
    get: operations["credentials_api_v1_settings_assistant_credentials_get"];
    /** Save */
    put: operations["save_api_v1_settings_assistant_credentials_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/assistant/credentials/unlock": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Unlock */
    post: operations["unlock_api_v1_settings_assistant_credentials_unlock_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/assistant/credentials/lock": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Lock */
    post: operations["lock_api_v1_settings_assistant_credentials_lock_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/assistant/connections/{connection_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test */
    post: operations["test_api_v1_settings_assistant_connections__connection_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/telemetry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Settings */
    get: operations["settings_api_v1_settings_telemetry_get"];
    /** Save */
    put: operations["save_api_v1_settings_telemetry_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/telemetry/status": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Status */
    get: operations["status_api_v1_settings_telemetry_status_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/telemetry/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test */
    post: operations["test_api_v1_settings_telemetry_test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/telemetry/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Retry */
    post: operations["retry_api_v1_settings_telemetry_retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/telemetry/links/{trace_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Link */
    get: operations["link_api_v1_settings_telemetry_links__trace_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/connections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Settings */
    get: operations["settings_api_v1_settings_connections_get"];
    /** Save Settings */
    put: operations["save_settings_api_v1_settings_connections_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/engines": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Engine Readiness */
    get: operations["engine_readiness_api_v1_settings_engines_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/voice-library": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Expressive Library */
    get: operations["expressive_library_api_v1_settings_voice_library_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/settings/comfy": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Comfy Status */
    get: operations["comfy_status_api_v1_settings_comfy_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/movies": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Movies */
    get: operations["movies_api_direction_movies_get"];
    put?: never;
    /** Create Movie */
    post: operations["create_movie_api_direction_movies_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/movies/{movie_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Movie */
    get: operations["movie_api_direction_movies__movie_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Movie */
    patch: operations["update_movie_api_direction_movies__movie_id__patch"];
    trace?: never;
  };
  "/api/direction/movies/{movie_id}/subjects": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Subjects */
    get: operations["subjects_api_direction_movies__movie_id__subjects_get"];
    put?: never;
    /** Add Subject */
    post: operations["add_subject_api_direction_movies__movie_id__subjects_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/subjects/{subject_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Subject */
    patch: operations["update_subject_api_direction_subjects__subject_id__patch"];
    trace?: never;
  };
  "/api/direction/subjects/{subject_id}/remove": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Remove Subject */
    post: operations["remove_subject_api_direction_subjects__subject_id__remove_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/movies/{movie_id}/segments": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Add Segment */
    post: operations["add_segment_api_direction_movies__movie_id__segments_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Segment */
    get: operations["segment_api_direction_segments__segment_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Segment */
    patch: operations["update_segment_api_direction_segments__segment_id__patch"];
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/assembled": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Assembled Segment */
    get: operations["assembled_segment_api_direction_segments__segment_id__assembled_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/use-set": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Use Set Preview */
    get: operations["use_set_preview_api_direction_segments__segment_id__use_set_get"];
    put?: never;
    /**
     * Use Set
     * @description Build the first shot's place as the set and ask the first shot for its views (ADR 0019 §5).
     */
    post: operations["use_set_api_direction_segments__segment_id__use_set_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/build-set": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Build Set Preview */
    get: operations["build_set_preview_api_direction_segments__segment_id__build_set_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/split-points": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Segment Split Points */
    get: operations["segment_split_points_api_direction_segments__segment_id__split_points_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/split": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Split Segment */
    post: operations["split_segment_api_direction_segments__segment_id__split_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/remove": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Remove Segment */
    post: operations["remove_segment_api_direction_segments__segment_id__remove_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/move": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Move Segment */
    post: operations["move_segment_api_direction_segments__segment_id__move_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/phases": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Add Phase */
    post: operations["add_phase_api_direction_segments__segment_id__phases_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/phases/{phase_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Phase */
    patch: operations["update_phase_api_direction_phases__phase_id__patch"];
    trace?: never;
  };
  "/api/direction/phases/{phase_id}/remove": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Remove Phase */
    post: operations["remove_phase_api_direction_phases__phase_id__remove_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/movies/{movie_id}/receipts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Receipts */
    get: operations["receipts_api_direction_movies__movie_id__receipts_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/receipts/{receipt_id}/undo": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Undo */
    post: operations["undo_api_direction_receipts__receipt_id__undo_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/places": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Places */
    get: operations["list_places_api_direction_places_get"];
    put?: never;
    /** Create Place */
    post: operations["create_place_api_direction_places_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/places/{place_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Place */
    get: operations["get_place_api_direction_places__place_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Edit Place Text */
    patch: operations["edit_place_text_api_direction_places__place_id__patch"];
    trace?: never;
  };
  "/api/direction/places/{place_id}/set": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Link Place Set
     * @description Build this place as a set, or unlink it (ADR 0019 §1).
     */
    put: operations["link_place_set_api_direction_places__place_id__set_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/places/{place_id}/angles": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Angles */
    get: operations["list_angles_api_direction_places__place_id__angles_get"];
    put?: never;
    /** Add Angle */
    post: operations["add_angle_api_direction_places__place_id__angles_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/angles/{angle_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Edit Angle Text */
    patch: operations["edit_angle_text_api_direction_angles__angle_id__patch"];
    trace?: never;
  };
  "/api/direction/angles/{angle_id}/evidence": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Angle Evidence */
    get: operations["angle_evidence_api_direction_angles__angle_id__evidence_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/evidence/{evidence_id}/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Approve Evidence */
    post: operations["approve_evidence_api_direction_evidence__evidence_id__approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/cuts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Create Cut */
    post: operations["create_cut_api_direction_cuts_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/cuts/{cut_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Cut */
    get: operations["get_cut_api_direction_cuts__cut_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Cut */
    patch: operations["update_cut_api_direction_cuts__cut_id__patch"];
    trace?: never;
  };
  "/api/direction/cuts/{cut_id}/keyframes": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Cut Keyframes */
    get: operations["cut_keyframes_api_direction_cuts__cut_id__keyframes_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/cuts/{cut_id}/remove": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Remove Cut */
    post: operations["remove_cut_api_direction_cuts__cut_id__remove_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/cuts/{cut_id}/suitability": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Cut Suitability */
    get: operations["cut_suitability_api_direction_cuts__cut_id__suitability_get"];
    put?: never;
    /** Judge Cut Suitability */
    post: operations["judge_cut_suitability_api_direction_cuts__cut_id__suitability_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/cuts/{cut_id}/plan": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Cut Plan */
    get: operations["get_cut_plan_api_direction_cuts__cut_id__plan_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/direction/segments/{segment_id}/plan": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Segment Plan */
    get: operations["get_segment_plan_api_direction_segments__segment_id__plan_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/people-analyses": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** People Analysis List */
    get: operations["people_analysis_list_api_production_people_analyses_get"];
    put?: never;
    /** People Analysis */
    post: operations["people_analysis_api_production_people_analyses_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/replacement-plans": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Replacement Preview */
    post: operations["replacement_preview_api_production_replacement_plans_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/replacements": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Replacement Start */
    post: operations["replacement_start_api_production_replacements_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/replacements/{operation_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Replacement Review */
    post: operations["replacement_review_api_production_replacements__operation_id__review_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/replacements/{operation_id}/plan": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Recorded Plan */
    get: operations["recorded_plan_api_production_replacements__operation_id__plan_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/replacements/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Replacement Approval */
    post: operations["replacement_approval_api_production_replacements_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/plates": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Empty Plate */
    post: operations["empty_plate_api_production_plates_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/removals": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Removal */
    post: operations["removal_api_production_removals_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/removals/{operation_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Plate Review */
    post: operations["plate_review_api_production_removals__operation_id__review_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/plates/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Plate Approval */
    post: operations["plate_approval_api_production_plates_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/surveys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Survey */
    post: operations["survey_api_production_surveys_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/surveys/{operation_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Survey Review */
    post: operations["survey_review_api_production_surveys__operation_id__review_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/set-views": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set View Renders */
    post: operations["set_view_renders_api_production_set_views_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/set-seeds": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Seed Start */
    post: operations["set_seed_start_api_production_set_seeds_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/set-seeds/{operation_id}/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Seed Retry */
    post: operations["set_seed_retry_api_production_set_seeds__operation_id__retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/set-views/{operation_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Views Review */
    post: operations["set_views_review_api_production_set_views__operation_id__review_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/views/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** View Approval */
    post: operations["view_approval_api_production_views_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/insertions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Insertion */
    post: operations["insertion_api_production_insertions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/insertions/{operation_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Insertion Review */
    post: operations["insertion_review_api_production_insertions__operation_id__review_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/pins/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Pin Approval */
    post: operations["pin_approval_api_production_pins_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/places/{place_id}/sheet-tiles": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Sheet Tiles */
    get: operations["sheet_tiles_api_production_places__place_id__sheet_tiles_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/sheets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Sheet */
    post: operations["sheet_api_production_sheets_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/renders": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Render */
    post: operations["render_api_production_renders_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/renders/{operation_id}/review": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Render Review */
    post: operations["render_review_api_production_renders__operation_id__review_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/operations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Recent Operations */
    get: operations["recent_operations_api_production_operations_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/operations/{operation_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Operation */
    get: operations["operation_api_production_operations__operation_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/operations/{operation_id}/summary": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Operation Summary */
    get: operations["operation_summary_api_production_operations__operation_id__summary_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/operations/{operation_id}/cancel": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel Operation */
    post: operations["cancel_operation_api_production_operations__operation_id__cancel_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/review-tags": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Taxonomy */
    get: operations["taxonomy_api_production_review_tags_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/candidates/{candidate_id}/prescreen": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Measure */
    post: operations["measure_api_production_candidates__candidate_id__prescreen_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/reviews/{review_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Blind Review */
    get: operations["blind_review_api_production_reviews__review_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/reviews/{review_id}/reveal": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Reveal Review */
    get: operations["reveal_review_api_production_reviews__review_id__reveal_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/reviews/{review_id}/reject": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reject References */
    post: operations["reject_references_api_production_reviews__review_id__reject_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/reviews/{review_id}/ranking": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Ranking */
    put: operations["ranking_api_production_reviews__review_id__ranking_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/reviews/{review_id}/select": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Select */
    post: operations["select_api_production_reviews__review_id__select_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/production/selections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Read Selection State */
    get: operations["read_selection_state_api_production_selections_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/export/movies/{movie_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Readiness */
    get: operations["readiness_api_export_movies__movie_id__get"];
    put?: never;
    /** Export */
    post: operations["export_api_export_movies__movie_id__post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Recasts */
    get: operations["recasts_api_recasts_get"];
    put?: never;
    /** Create */
    post: operations["create_api_recasts_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get */
    get: operations["get_api_recasts__recast_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/estimate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Estimate
     * @description What a run over a range would cost in a mode, before it starts (the run's own estimate covers the last
     *     run's range).
     */
    get: operations["estimate_api_recasts__recast_id__estimate_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Patch Character */
    patch: operations["patch_character_api_recasts__recast_id__characters__character_id__patch"];
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/merge": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Merge */
    post: operations["merge_api_recasts__recast_id__characters_merge_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/not-same": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Not Same */
    post: operations["not_same_api_recasts__recast_id__not_same_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/tracks/move": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Move Tracks */
    post: operations["move_tracks_api_recasts__recast_id__tracks_move_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/edits/{edit_id}/undo": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Undo Edit
     * @description Human only: the guarded inverse of one cast edit. Redo is undo of the inverse entry.
     */
    post: operations["undo_edit_api_recasts__recast_id__edits__edit_id__undo_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}/propose": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Propose Appearance */
    post: operations["propose_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__propose_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Confirm Appearance */
    post: operations["confirm_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__confirm_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}/reject": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reject Appearance */
    post: operations["reject_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__reject_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Clear Appearance */
    delete: operations["clear_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/appearances/suggest": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Suggest Appearances */
    post: operations["suggest_appearances_api_recasts__recast_id__shots__shot_id__appearances_suggest_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/target": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Set Target
     * @description A human mapping: confirmed at once.
     */
    put: operations["set_target_api_recasts__recast_id__characters__character_id__target_put"];
    post?: never;
    /** Clear Target */
    delete: operations["clear_target_api_recasts__recast_id__characters__character_id__target_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/target/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Confirm Target */
    post: operations["confirm_target_api_recasts__recast_id__characters__character_id__target_confirm_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/reference/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Approve Reference */
    post: operations["approve_reference_api_recasts__recast_id__characters__character_id__reference_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/reference/reject": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reject Reference */
    post: operations["reject_reference_api_recasts__recast_id__characters__character_id__reference_reject_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/voice": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Set Voice
     * @description Human only: the character's voice recordings, confirmed at once (journaled). Building asks separately.
     */
    put: operations["set_voice_api_recasts__recast_id__characters__character_id__voice_put"];
    post?: never;
    /**
     * Clear Voice
     * @description Human only: remove the recordings, or reject proposed ones (journaled).
     */
    delete: operations["clear_voice_api_recasts__recast_id__characters__character_id__voice_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/voice/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Confirm Voice
     * @description Human only: confirm proposed recordings (journaled).
     */
    post: operations["confirm_voice_api_recasts__recast_id__characters__character_id__voice_confirm_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/voice/build": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Build Voice
     * @description Human only: start the voice build (GPU hours; the client asks first). Never automatic.
     */
    post: operations["build_voice_api_recasts__recast_id__characters__character_id__voice_build_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/voice/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Retry Voice
     * @description Human only: resume a stopped build, start the next attempt of a failed one, or audition again.
     */
    post: operations["retry_voice_api_recasts__recast_id__characters__character_id__voice_retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/voice/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Approve Voice
     * @description Human only: approve the audition; the reel records the generated voice's provenance.
     */
    post: operations["approve_voice_api_recasts__recast_id__characters__character_id__voice_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/characters/{character_id}/voice/reject": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Reject Voice
     * @description Human only: reject the audition.
     */
    post: operations["reject_voice_api_recasts__recast_id__characters__character_id__voice_reject_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Patch Shot */
    patch: operations["patch_shot_api_recasts__recast_id__shots__shot_id__patch"];
    trace?: never;
  };
  "/api/recasts/{recast_id}/plan": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Plan */
    post: operations["plan_api_recasts__recast_id__plan_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/run": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Run */
    post: operations["run_api_recasts__recast_id__run_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/analysis/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Retry Analysis */
    post: operations["retry_analysis_api_recasts__recast_id__analysis_retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/run/pause": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Pause */
    post: operations["pause_api_recasts__recast_id__run_pause_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/run/cancel": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel */
    post: operations["cancel_api_recasts__recast_id__run_cancel_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/choice": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Choose
     * @description Human only: the shot version the conform uses, or "original".
     */
    post: operations["choose_api_recasts__recast_id__shots__shot_id__choice_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/head-pass": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Head Pass */
    post: operations["head_pass_api_recasts__recast_id__shots__shot_id__head_pass_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/retake": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Retake */
    post: operations["retake_api_recasts__recast_id__shots__shot_id__retake_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/shots/{shot_id}/reveal": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Reveal */
    get: operations["reveal_api_recasts__recast_id__shots__shot_id__reveal_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/dialogue": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Start Dialogue
     * @description Human only: start the film's shared dialogue analysis (asks first; never automatic).
     */
    post: operations["start_dialogue_api_recasts__recast_id__dialogue_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/dialogue/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Retry Dialogue
     * @description Human only: a new attempt after the dialogue analysis failed or was cancelled.
     */
    post: operations["retry_dialogue_api_recasts__recast_id__dialogue_retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/lines": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Lines */
    get: operations["lines_api_recasts__recast_id__lines_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/lines/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Confirm Lines
     * @description Human only: confirm a character's proposed lines in one journaled edit.
     */
    post: operations["confirm_lines_api_recasts__recast_id__lines_confirm_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/lines/{segment_key}/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Confirm Line
     * @description Human only: this line is the character's.
     */
    post: operations["confirm_line_api_recasts__recast_id__lines__segment_key__confirm_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/lines/{segment_key}/reject": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Reject Line
     * @description Human only: this line is not the character's.
     */
    post: operations["reject_line_api_recasts__recast_id__lines__segment_key__reject_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/lines/{segment_key}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /**
     * Clear Line
     * @description Human only: remove the decision on a line; the computed proposal returns.
     */
    delete: operations["clear_line_api_recasts__recast_id__lines__segment_key__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/lines/{segment_key}/edges": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Edge Line
     * @description Human only: the line's edges in 48 kHz samples (confirms it); both null removes them.
     */
    put: operations["edge_line_api_recasts__recast_id__lines__segment_key__edges_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/conform": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Conform
     * @description Human only: the film with chosen versions (or labelled unreviewed drafts) pasted in.
     */
    post: operations["conform_api_recasts__recast_id__conform_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/recasts/{recast_id}/faces/{artifact_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Face */
    get: operations["face_api_recasts__recast_id__faces__artifact_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Sets */
    get: operations["list_sets_api_sets_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/import": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Import Set */
    post: operations["import_set_api_sets_import_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Set */
    get: operations["get_set_api_sets__set_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/versions/{version_id}/map": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Map */
    get: operations["get_map_api_sets__set_id__versions__version_id__map_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/issues": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Mark Issue */
    post: operations["mark_issue_api_sets__set_id__issues_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/issues/{issue_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Issue Status */
    patch: operations["issue_status_api_sets__set_id__issues__issue_id__patch"];
    trace?: never;
  };
  "/api/sets/{set_id}/fixes": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Queue Fix */
    post: operations["queue_fix_api_sets__set_id__fixes_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/fill-more": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Fill More
     * @description Queue an extension that fills the stage's unseen space all round (ADR 0020 §4).
     */
    post: operations["fill_more_api_sets__set_id__fill_more_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/fixes/{fix_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Cancel Fix */
    delete: operations["cancel_fix_api_sets__set_id__fixes__fix_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/fixes/{fix_id}/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Retry Fix
     * @description Human-only: queue a fix again after its build stopped.
     */
    post: operations["retry_fix_api_sets__set_id__fixes__fix_id__retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/build": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Run Fixes
     * @description Human-only (ADR 0018 §5): run the queued fixes on this version; the result is a proposed version.
     */
    post: operations["run_fixes_api_sets__set_id__build_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/hide-regions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Hide Region */
    post: operations["hide_region_api_sets__set_id__hide_regions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/hide-regions/{region_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Hide Region Removed */
    patch: operations["hide_region_removed_api_sets__set_id__hide_regions__region_id__patch"];
    trace?: never;
  };
  "/api/sets/{set_id}/views": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Save View */
    post: operations["save_view_api_sets__set_id__views_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/views/{view_id}/still": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * View Still
     * @description The picture of a saved view as the workbench drew it: a Library image with `set_view` provenance.
     */
    post: operations["set_view_still"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/places": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Places Built As */
    get: operations["places_built_as_api_sets__set_id__places_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/views/{view_id}/angle": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Aim View
     * @description Make the view the camera of a place's angle, or of none (ADR 0019 §2).
     */
    put: operations["aim_view_api_sets__set_id__views__view_id__angle_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/views/{view_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** View Removed */
    patch: operations["view_removed_api_sets__set_id__views__view_id__patch"];
    trace?: never;
  };
  "/api/sets/{set_id}/versions/{version_id}/accept": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Accept */
    post: operations["accept_api_sets__set_id__versions__version_id__accept_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/versions/{version_id}/discard": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discard */
    post: operations["discard_api_sets__set_id__versions__version_id__discard_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/sets/{set_id}/splats/{artifact_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Splat */
    get: operations["splat_api_sets__set_id__splats__artifact_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/model": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Model Status */
    get: operations["model_status_api_agent_model_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Sessions */
    get: operations["sessions_api_agent_sessions_get"];
    put?: never;
    /** Open Session */
    post: operations["open_session_api_agent_sessions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/new": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** New Conversation */
    post: operations["new_conversation_api_agent_sessions__session_id__new_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/plan/stop": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Stop Plan */
    post: operations["stop_plan_api_agent_sessions__session_id__plan_stop_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Session */
    get: operations["get_session_api_agent_sessions__session_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/messages": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Message */
    post: operations["message_api_agent_sessions__session_id__messages_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/board": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Board */
    put: operations["board_api_agent_sessions__session_id__board_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/cancel": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel */
    post: operations["cancel_api_agent_sessions__session_id__cancel_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/plan/approve": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Approve Plan */
    post: operations["approve_plan_api_agent_sessions__session_id__plan_approve_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/plan/revise": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revise Plan */
    post: operations["revise_plan_api_agent_sessions__session_id__plan_revise_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/plan/reject": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reject Plan */
    post: operations["reject_plan_api_agent_sessions__session_id__plan_reject_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/agent/sessions/{session_id}/plan-mode": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Plan Mode */
    put: operations["plan_mode_api_agent_sessions__session_id__plan_mode_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}
export type webhooks = Record<string, never>;
export interface components {
  schemas: {
    /**
     * Action
     * @enum {string}
     */
    Action:
      | "declare_relation"
      | "view_angle"
      | "review_view"
      | "view_place"
      | "insert_subject"
      | "review_keyframe"
      | "state_sides";
    /**
     * AgentBudgets
     * @description Turn counts are the meaningful guard; token budgets are a runaway backstop.
     *
     *     A turn costs at most one context window, so the token ceilings are sized above
     *     ``max_session_turns`` full turns. Sizing them below that made a session stop mid-message
     *     with "input token budget reached" for reasons unrelated to the work being done.
     */
    AgentBudgets: {
      /**
       * Max Turns Per Message
       * @default 24
       */
      max_turns_per_message: number;
      /**
       * Max Session Turns
       * @default 400
       */
      max_session_turns: number;
      /**
       * Max Input Tokens
       * @default 4000000
       */
      max_input_tokens: number;
      /**
       * Max Output Tokens
       * @default 500000
       */
      max_output_tokens: number;
      /**
       * Request Turns
       * @default 0
       */
      request_turns: number;
      /**
       * Turns Spent
       * @default 0
       */
      turns_spent: number;
      /**
       * Input Tokens Spent
       * @default 0
       */
      input_tokens_spent: number;
      /**
       * Output Tokens Spent
       * @default 0
       */
      output_tokens_spent: number;
    };
    /**
     * AgentBuildDTO
     * @description The plan's stage build in Python's words, for the chat (ADR 0020 §8).
     */
    AgentBuildDTO: {
      /**
       * State
       * @enum {string}
       */
      state: "building" | "refused" | "failed" | "done";
      /** Name */
      name: string;
      /** Label */
      label: string;
      /** Message */
      message: string;
      /** Operation Id */
      operation_id: string;
      /** Set Id */
      set_id: string | null;
      /** Shot */
      shot: number | null;
      /** Waiting */
      waiting: boolean;
      /** Can Retry */
      can_retry: boolean;
      /** Step */
      step: number;
      /** Note */
      note: string;
    };
    /** AgentConversationRevisionInput */
    AgentConversationRevisionInput: {
      /** Expected Revision */
      expected_revision: number;
    };
    /** AgentModelStatusDTO */
    AgentModelStatusDTO: {
      /** Provider */
      provider: string;
      /** Connection Id */
      connection_id: string;
      /** Model */
      model: string;
      /**
       * State
       * @enum {string}
       */
      state: "unloaded" | "waiting_gpu" | "loading" | "ready" | "thinking";
      /** Since */
      since: string;
      /** Session Id */
      session_id: string | null;
      /** Keep Warm Seconds */
      keep_warm_seconds: number;
    };
    /** AgentPlanDTO */
    AgentPlanDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /**
       * State
       * @default proposed
       * @enum {string}
       */
      state:
        | "proposed"
        | "approved"
        | "executing"
        | "done"
        | "failed"
        | "revised"
        | "rejected"
        | "superseded";
      /** Goal */
      goal: string;
      /** Steps */
      steps: components["schemas"]["AgentPlanStepDTO"][];
      /** Budget Jobs */
      budget_jobs: number;
      /** Questions */
      questions?: string[];
      /**
       * Note
       * @default
       */
      note: string;
      /** Proposed Turn */
      proposed_turn: number;
      grant?: components["schemas"]["AgentPlanGrantDTO"] | null;
      /**
       * Revision Requested
       * @default false
       */
      revision_requested: boolean;
    };
    /** AgentPlanDecisionInput */
    AgentPlanDecisionInput: {
      /** Expected Revision */
      expected_revision: number;
      /**
       * Note
       * @default
       */
      note: string;
    };
    /** AgentPlanGrantDTO */
    AgentPlanGrantDTO: {
      /** Budget Jobs */
      budget_jobs: number;
      /** Step Kinds */
      step_kinds: (
        "edit" | "prepare" | "render" | "pick" | "review" | "answer" | "build"
      )[];
      /** Approved Turn */
      approved_turn: number;
      /** Reserved At Approval */
      reserved_at_approval: number;
    };
    /** AgentPlanModeInput */
    AgentPlanModeInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Enabled */
      enabled: boolean;
    };
    /** AgentPlanReviseInput */
    AgentPlanReviseInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Note */
      note: string;
    };
    /** AgentPlanStepDTO */
    AgentPlanStepDTO: {
      /**
       * Kind
       * @enum {string}
       */
      kind:
        "edit" | "prepare" | "render" | "pick" | "review" | "answer" | "build";
      /** Title */
      title: string;
      /**
       * Target
       * @default
       */
      target: string;
      /**
       * Jobs
       * @default 0
       */
      jobs: number;
      /**
       * Status
       * @default pending
       * @enum {string}
       */
      status: "pending" | "done" | "skipped" | "awaiting_user";
      /** Receipts */
      receipts?: {
        [key: string]: unknown;
      }[];
      /**
       * Review Of
       * @default
       */
      review_of: string;
    };
    /** AnalysisStatusDTO */
    AnalysisStatusDTO: {
      /** Operation Id */
      operation_id: string | null;
      /**
       * State
       * @enum {string}
       */
      state: "not_started" | "running" | "succeeded" | "failed";
      /** Stage */
      stage: ("shots" | "faces" | "cast" | "done") | null;
      /** Done */
      done: number;
      /** Total */
      total: number;
      /** Error */
      error: string | null;
    };
    /** AngleDTO */
    AngleDTO: {
      /** Id */
      id: string;
      /** Place Id */
      place_id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Revision */
      revision: number;
      /** Placement */
      placement: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
    };
    /** AngleInput */
    AngleInput: {
      /** Name */
      name: string;
      /** Placement */
      placement?: {
        [key: string]: unknown;
      } | null;
    };
    /** AppearanceDTO */
    AppearanceDTO: {
      /** Character Id */
      character_id: string | null;
      /** Track Ids */
      track_ids: string[];
      /** Frames With Face */
      frames_with_face: number;
      /** Coverage */
      coverage: number;
      /** Confirmed */
      confirmed: boolean;
    };
    /** AppearanceDecisionInput */
    AppearanceDecisionInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Character Id */
      character_id: string;
    };
    /** AppearanceLinkDTO */
    AppearanceLinkDTO: {
      /** Track Id */
      track_id: string;
      /** Analysis Operation Id */
      analysis_operation_id: string;
      /** Character Id */
      character_id: string;
      /**
       * State
       * @enum {string}
       */
      state: "proposed" | "confirmed" | "rejected";
      /**
       * Proposed By
       * @enum {string}
       */
      proposed_by: "analysis" | "agent" | "user";
      /** Evidence */
      evidence?: {
        [key: string]: unknown;
      };
      /** Created At */
      created_at: string;
      /** Decided At */
      decided_at: string | null;
    };
    /** AppearanceProposalInput */
    AppearanceProposalInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Character Id */
      character_id: string;
      /**
       * Proposed By
       * @default user
       * @enum {string}
       */
      proposed_by: "analysis" | "user";
      /** Evidence */
      evidence?: {
        [key: string]: unknown;
      };
    };
    /** ArchivedLibraryDTO */
    ArchivedLibraryDTO: {
      /** Path */
      path: string;
      /** Revision */
      revision: string;
      /** Archived At */
      archived_at: string;
    };
    /** AssembledSegmentDTO */
    AssembledSegmentDTO: {
      /** Subjects */
      subjects: components["schemas"]["AuthoredSubjectDTO"][];
      place: components["schemas"]["PlaceTextDTO"];
      /** Shots */
      shots: components["schemas"]["ShotDTO"][];
      /** Cut Frames */
      cut_frames: number[];
      /** Delivered Frames */
      delivered_frames: number;
      /** Continues */
      continues: boolean;
      /** Style */
      style?: string;
      /** Phases */
      phases?: components["schemas"]["LaterPhaseDTO"][];
      /** Events */
      events?: components["schemas"]["EventDTO"][];
      /** Speech */
      speech?: components["schemas"]["SpeechDTO"][];
      /** States */
      states?: components["schemas"]["StateDTO"][];
    };
    /** AssessmentDTO */
    AssessmentDTO: {
      coverage: components["schemas"]["Coverage"];
      /** Reasons */
      reasons: string[];
      /** Evidence Ids */
      evidence_ids: string[];
      /** Coverage Words */
      readonly coverage_words: string;
    };
    /** AssetDTO */
    AssetDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "image" | "video" | "audio";
      /** Sha256 */
      sha256: string;
      /** Mime */
      mime: string;
      /** Info */
      info: {
        [key: string]: unknown;
      };
      /** Provenance */
      provenance: {
        [key: string]: unknown;
      }[];
      /** Review */
      review: string;
      /** Created At */
      created_at: string;
    };
    /** AssetPage */
    AssetPage: {
      /** Items */
      items: components["schemas"]["AssetDTO"][];
      /** Total */
      total: number;
      /** Offset */
      offset: number;
      /** Limit */
      limit: number;
    };
    /** AssistantConnection */
    AssistantConnection: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /**
       * Provider
       * @default openai_compatible
       * @constant
       */
      provider: "openai_compatible";
      /** Base Url */
      base_url: string;
      /** Model */
      model: string;
      /** Credential Id */
      credential_id?: string | null;
      /**
       * Vision
       * @default true
       */
      vision: boolean;
      /**
       * Video Input
       * @default none
       * @enum {string}
       */
      video_input: "none" | "video_url";
      /**
       * Structured Output
       * @default json_schema
       * @enum {string}
       */
      structured_output: "json_schema" | "json_object";
      /**
       * Reasoning
       * @default none
       * @enum {string}
       */
      reasoning: "none" | "reasoning_effort" | "openrouter";
      /**
       * Sampling
       * @default true
       */
      sampling: boolean;
      /**
       * Streaming
       * @default false
       */
      streaming: boolean;
      /**
       * Context Tokens
       * @default 32768
       */
      context_tokens: number;
      /**
       * Max Output Tokens
       * @default 4096
       */
      max_output_tokens: number;
      /** Input Cost Per Million */
      input_cost_per_million?: number | null;
      /** Output Cost Per Million */
      output_cost_per_million?: number | null;
    };
    /** AssociationInput */
    AssociationInput: {
      /** Asset Id */
      asset_id: string;
      /**
       * Role
       * @default reference
       */
      role: string;
    };
    /** AuthoredSubjectDTO */
    AuthoredSubjectDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Keeps */
      keeps: string[];
      /** Identity Asset */
      identity_asset: string | null;
    };
    /** BoardEntry */
    BoardEntry: {
      /** Id */
      id: string;
      /**
       * Text
       * @default
       */
      text: string;
      /**
       * Summary
       * @default
       */
      summary: string;
      /** Turn */
      turn?: number | null;
      /**
       * Resolved
       * @default false
       */
      resolved: boolean;
    };
    /** BoardInput */
    BoardInput: {
      /** Expected Revision */
      expected_revision: number;
      board: components["schemas"]["Whiteboard"];
    };
    /** Body_import_media_api_v1_imports_post */
    Body_import_media_api_v1_imports_post: {
      /** File */
      file: string;
      /** Collection Id */
      collection_id?: string | null;
      /** Entity Id */
      entity_id?: string | null;
      /**
       * Role
       * @default reference
       */
      role: string;
    };
    /** Body_set_view_still */
    Body_set_view_still: {
      /** File */
      file: string;
    };
    /**
     * BuildSetPreviewDTO
     * @description What Build a set from this take… would do, said before it does.
     */
    BuildSetPreviewDTO: {
      /** Shots */
      shots: components["schemas"]["SeedShotDTO"][];
      /** Shot */
      shot: number;
      /** Frames */
      frames: number;
      /** Fill Unseen */
      fill_unseen: boolean;
      /** Renders */
      renders: number;
      /** Estimate */
      estimate: string;
      /** What */
      what: string;
      /** Name */
      name: string;
      /** Candidate Id */
      candidate_id: string | null;
      place: components["schemas"]["SeedPlaceDTO"] | null;
      /** Can Use For Place */
      can_use_for_place: boolean;
      /** Refusal */
      refusal: string | null;
      seed: components["schemas"]["SeedStateDTO"] | null;
    };
    /** BuildVoiceInput */
    BuildVoiceInput: {
      /** Expected Revision */
      expected_revision: number;
      /**
       * Rebuild
       * @default false
       */
      rebuild: boolean;
    };
    /** BulkConfirmInput */
    BulkConfirmInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Character Id */
      character_id: string;
      /** Segment Keys */
      segment_keys?: string[] | null;
    };
    /** CandidateDTO */
    CandidateDTO: {
      prescreen: components["schemas"]["PrescreenDTO"] | null;
      /** Id */
      id: string;
      /** Job Id */
      job_id: string;
      /** Operation Id */
      operation_id: string;
      /** Asset Id */
      asset_id: string;
      /** Artifact Id */
      artifact_id: string | null;
      /** Output Key */
      output_key: string;
      /** Kind */
      kind: string;
      /** Created At */
      created_at: string;
    };
    /** CandidateInput */
    CandidateInput: {
      /** Candidate Id */
      candidate_id: string;
    };
    /** CharacterDTO */
    CharacterDTO: {
      /** Id */
      id: string;
      /** Label */
      label: string;
      /** Hidden */
      hidden: boolean;
      face: components["schemas"]["FaceDTO"] | null;
      /** Screen Time Frames */
      screen_time_frames: number;
      /** Shot Count */
      shot_count: number;
      /** Presence */
      presence: number[][];
      /** Track Count */
      track_count: number;
      /** Tracks */
      tracks: components["schemas"]["CharacterTrackDTO"][];
      target: components["schemas"]["TargetDTO"] | null;
      /** Similar */
      similar?: components["schemas"]["SimilarCharacterDTO"][];
      /**
       * Lines Proposed
       * @default 0
       */
      lines_proposed: number;
      /**
       * Lines Confirmed
       * @default 0
       */
      lines_confirmed: number;
      voice?: components["schemas"]["VoiceDTO"] | null;
    };
    /** CharacterPatchInput */
    CharacterPatchInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Label */
      label?: string | null;
      /** Hidden */
      hidden?: boolean | null;
    };
    /**
     * CharacterTrackDTO
     * @description One face track of a character, for the drawer that splits or moves tracks.
     */
    CharacterTrackDTO: {
      /** Id */
      id: string;
      /** Shot Id */
      shot_id: string;
      /** Frames With Face */
      frames_with_face: number;
      face: components["schemas"]["FaceDTO"] | null;
    };
    /** ChoiceDTO */
    ChoiceDTO: {
      /** Technique Id */
      technique_id: string;
      /** Title */
      title: string;
      status: components["schemas"]["Status"];
      /** Blocks */
      blocks: (
        "direction_text" | "side_facts_text" | "place_views" | "pinned_frame"
      )[];
      /** Ready */
      ready: boolean;
      /** Actions */
      actions: components["schemas"]["NextActionDTO"][];
      /** Keyframe Offset Frames */
      keyframe_offset_frames: number | null;
      /** Evidence */
      evidence: string[];
      /** Limitations */
      limitations: string[];
    };
    /**
     * ChoiceInput
     * @description A version id, "original" (keep the shot as filmed) or "unchosen" (clear the choice; undo).
     */
    ChoiceInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Version */
      version: string;
    };
    /** ComfyStatusDTO */
    ComfyStatusDTO: {
      /** Connected */
      connected: boolean;
      /** Version */
      version?: string | null;
      /** Error */
      error?: string | null;
    };
    /** ConformDTO */
    ConformDTO: {
      /** Operation Id */
      operation_id: string;
      /** State */
      state: string;
      /**
       * Mode
       * @enum {string}
       */
      mode: "chosen" | "preview_unreviewed";
      range: components["schemas"]["FrameRangeDTO"] | null;
      /** Progress */
      progress: number;
      /** Asset Id */
      asset_id: string | null;
      /** Created At */
      created_at: string;
      /**
       * Codec
       * @default h264
       * @enum {string}
       */
      codec: "h264" | "prores";
      /** Error */
      error?: string | null;
    };
    /** ConformInput */
    ConformInput: {
      /**
       * Mode
       * @default chosen
       * @enum {string}
       */
      mode: "chosen" | "preview_unreviewed";
      range?: components["schemas"]["FrameRangeDTO"] | null;
      /**
       * Codec
       * @default h264
       * @enum {string}
       */
      codec: "h264" | "prores";
      /** Request Key */
      request_key: string;
    };
    /** ConnectionProbe */
    ConnectionProbe: {
      /** Modality */
      modality?: ("text" | "image" | "video") | null;
    };
    /** ConnectionResult */
    ConnectionResult: {
      /** Ok */
      ok: boolean;
      /** Project Id */
      project_id: string;
      /** Project Name */
      project_name: string;
    };
    /** ConnectionSettings */
    ConnectionSettings: {
      /**
       * Comfy Url
       * @default http://127.0.0.1:8188
       */
      comfy_url: string;
      /**
       * Assistant Executable
       * @default
       */
      assistant_executable: string;
      /**
       * Assistant Model
       * @default
       */
      assistant_model: string;
      /**
       * Assistant Projector
       * @default
       */
      assistant_projector: string;
      /**
       * Assistant Context
       * @default 8192
       */
      assistant_context: number;
      /**
       * Assistant Agent Context
       * @default 32768
       */
      assistant_agent_context: number;
      /** Assistant Connections */
      assistant_connections?: components["schemas"]["AssistantConnection"][];
      /**
       * Assistant Agent Connection
       * @default local
       */
      assistant_agent_connection: string;
      /**
       * Assistant Agent Thinking
       * @default inherit
       * @enum {string}
       */
      assistant_agent_thinking: "inherit" | "off" | "low" | "medium" | "xhigh";
      /**
       * Agent Autonomy
       * @default off
       * @enum {string}
       */
      agent_autonomy: "off" | "prepare" | "render";
      /**
       * Agent Budget Jobs
       * @default 6
       */
      agent_budget_jobs: number;
      /**
       * Agent Keep Warm Seconds
       * @default 180
       */
      agent_keep_warm_seconds: number;
      /**
       * Engines Root
       * @default
       */
      engines_root: string;
      /**
       * Expressive Library
       * @default
       */
      expressive_library: string;
    };
    /** ConnectionTest */
    ConnectionTest: {
      /** Ok */
      ok: boolean;
      /** Model */
      model: string;
      /** Vision Input Tested */
      vision_input_tested: boolean;
      /**
       * Video Input Tested
       * @default false
       */
      video_input_tested: boolean;
      /**
       * Structured Output Tested
       * @default true
       */
      structured_output_tested: boolean;
      /**
       * Modality
       * @default image
       */
      modality: string;
    };
    /**
     * Coverage
     * @enum {string}
     */
    Coverage: "continuing" | "established" | "new" | "unknown";
    /** CreateLibrary */
    CreateLibrary: {
      /** Name */
      name: string;
    };
    /** CreateRecastInput */
    CreateRecastInput: {
      /** Source Asset Id */
      source_asset_id: string;
      /** Title */
      title?: string | null;
      /** Request Key */
      request_key: string;
    };
    /** CredentialState */
    CredentialState: {
      /** Exists */
      exists: boolean;
      /** Unlocked */
      unlocked: boolean;
      /** Credential Ids */
      credential_ids: string[];
      /** Session Credential Ids */
      session_credential_ids?: string[];
    };
    /** CutBindingDTO */
    CutBindingDTO: {
      /**
       * Kind
       * @enum {string}
       */
      kind: "view" | "pin" | "sheet";
      /** Id */
      id: string;
      /** Asset Id */
      asset_id: string;
      /** Approved At */
      approved_at: string | null;
      /** Stale */
      stale: boolean;
      /** Reasons */
      reasons: string[];
      /**
       * Suitability
       * @enum {string}
       */
      suitability: "suits" | "unsuitable" | "unjudged";
      /** Suitability Note */
      suitability_note: string;
    };
    /** CutCommandDTO */
    CutCommandDTO: {
      /** Segment Id */
      segment_id: string;
      /** Frame */
      frame: number;
      /** From Angle Id */
      from_angle_id: string;
      /** To Angle Id */
      to_angle_id: string;
      /** Relation */
      relation?: string | null;
      /** Crosses Barrier */
      crosses_barrier?: boolean | null;
      /**
       * Relation Source
       * @default unknown
       */
      relation_source: string;
      /**
       * Receives Predecessor
       * @default false
       */
      receives_predecessor: boolean;
      /**
       * Side Facts
       * @default false
       */
      side_facts: boolean;
      /**
       * Fidelity
       * @default false
       */
      fidelity: boolean;
      /** Override Technique */
      override_technique?: string | null;
      /** Insertion */
      insertion?: {
        [key: string]: unknown;
      } | null;
      /** Movie Id */
      movie_id: string;
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Updated At */
      updated_at: string;
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** CutCreate */
    CutCreate: {
      /** Segment Id */
      segment_id: string;
      /** Frame */
      frame: number;
      /** From Angle Id */
      from_angle_id: string;
      /** To Angle Id */
      to_angle_id: string;
      /** Relation */
      relation?: string | null;
      /** Crosses Barrier */
      crosses_barrier?: boolean | null;
      /**
       * Relation Source
       * @default unknown
       */
      relation_source: string;
      /**
       * Receives Predecessor
       * @default false
       */
      receives_predecessor: boolean;
      /**
       * Side Facts
       * @default false
       */
      side_facts: boolean;
      /**
       * Fidelity
       * @default false
       */
      fidelity: boolean;
      /** Override Technique */
      override_technique?: string | null;
      /** Expected Revision */
      expected_revision: number;
    };
    /** CutDTO */
    CutDTO: {
      /** Segment Id */
      segment_id: string;
      /** Frame */
      frame: number;
      /** From Angle Id */
      from_angle_id: string;
      /** To Angle Id */
      to_angle_id: string;
      /** Relation */
      relation?: string | null;
      /** Crosses Barrier */
      crosses_barrier?: boolean | null;
      /**
       * Relation Source
       * @default unknown
       */
      relation_source: string;
      /**
       * Receives Predecessor
       * @default false
       */
      receives_predecessor: boolean;
      /**
       * Side Facts
       * @default false
       */
      side_facts: boolean;
      /**
       * Fidelity
       * @default false
       */
      fidelity: boolean;
      /** Override Technique */
      override_technique?: string | null;
      /** Insertion */
      insertion?: {
        [key: string]: unknown;
      } | null;
      /** Movie Id */
      movie_id: string;
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Updated At */
      updated_at: string;
    };
    /** CutPatch */
    CutPatch: {
      /** Expected Revision */
      expected_revision: number;
      /** Frame */
      frame?: number | null;
      /** From Angle Id */
      from_angle_id?: string | null;
      /** To Angle Id */
      to_angle_id?: string | null;
      /** Relation */
      relation?: string | null;
      /** Crosses Barrier */
      crosses_barrier?: boolean | null;
      /** Relation Source */
      relation_source?: string | null;
      /** Receives Predecessor */
      receives_predecessor?: boolean | null;
      /** Side Facts */
      side_facts?: boolean | null;
      /** Fidelity */
      fidelity?: boolean | null;
      /** Override Technique */
      override_technique?: string | null;
      /** Insertion */
      insertion?: {
        [key: string]: unknown;
      } | null;
    };
    /** CutPlanDTO */
    CutPlanDTO: {
      /** Bindings */
      bindings: components["schemas"]["CutBindingDTO"][];
      /** Cut Id */
      cut_id: string;
      assessment: components["schemas"]["AssessmentDTO"];
      recommended: components["schemas"]["ChoiceDTO"] | null;
      /** Alternatives */
      alternatives: components["schemas"]["ChoiceDTO"][];
      /** Unavailable */
      unavailable: [string, string][];
      /** Gaps */
      gaps: components["schemas"]["GapDTO"][];
      /** Override */
      override: boolean;
    };
    /** CutSuitabilityDTO */
    CutSuitabilityDTO: {
      /** Id */
      id: string;
      /** Cut Id */
      cut_id: string;
      /**
       * Reference Kind
       * @enum {string}
       */
      reference_kind: "view" | "pin" | "sheet";
      /** Reference Id */
      reference_id: string;
      /**
       * Verdict
       * @enum {string}
       */
      verdict: "suits" | "unsuitable";
      /** Note */
      note: string;
      /** Judged At */
      judged_at: string;
      /** Judged By */
      judged_by: string;
      /** Superseded At */
      superseded_at: string | null;
      /** Stale */
      stale: boolean;
    };
    /** CutSuitabilityInput */
    CutSuitabilityInput: {
      /**
       * Reference Kind
       * @enum {string}
       */
      reference_kind: "view" | "pin" | "sheet";
      /** Reference Id */
      reference_id: string;
      /**
       * Verdict
       * @enum {string}
       */
      verdict: "suits" | "unsuitable";
      /**
       * Note
       * @default
       */
      note: string;
    };
    /**
     * DialogueStatusDTO
     * @description The film's shared dialogue analysis (ADR 0016 §2), as the Voice step reads it.
     */
    DialogueStatusDTO: {
      /** Operation Id */
      operation_id: string | null;
      /**
       * State
       * @enum {string}
       */
      state: "not_started" | "running" | "succeeded" | "failed";
      /** Stage */
      stage: ("audio" | "separate" | "transcribe" | "speakers" | "done") | null;
      /** Done */
      done: number;
      /** Total */
      total: number;
      /** Error */
      error: string | null;
      /** Attempt */
      attempt: number;
      /** Version */
      version: number;
      /** Sample Rate */
      sample_rate: number;
      /** Reason */
      reason: string | null;
      gap: components["schemas"]["RecastGapDTO"] | null;
    };
    /** DirectionAngleTextCommandDTO */
    DirectionAngleTextCommandDTO: {
      /** Id */
      id: string;
      /** Place Id */
      place_id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Revision */
      revision: number;
      /** Placement */
      placement: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** DirectionAngleTextPatch */
    DirectionAngleTextPatch: {
      /** Movie Id */
      movie_id: string;
      /** Expected Revision */
      expected_revision: number;
      /** Name */
      name: string;
      /** Description */
      description: string;
    };
    /** DirectionMovieDTO */
    DirectionMovieDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Title */
      title: string;
    };
    /** DirectionPlaceTextCommandDTO */
    DirectionPlaceTextCommandDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Sound */
      sound: string;
      /** Overhead */
      overhead: string;
      /** Revision */
      revision: number;
      /** Plate Asset Id */
      plate_asset_id: string | null;
      /** Plate Angle Id */
      plate_angle_id: string | null;
      /** Set Id */
      set_id?: string | null;
      /** Created At */
      created_at: string;
      receipt: components["schemas"]["ReceiptDTO"];
      /**
       * View Producer
       * @description Who makes this place's reviewed angle views (application/prepare_references.view_producer).
       * @enum {string}
       */
      readonly view_producer: "survey" | "set_views";
    };
    /** DirectionPlaceTextPatch */
    DirectionPlaceTextPatch: {
      /** Movie Id */
      movie_id: string;
      /** Expected Revision */
      expected_revision: number;
      /** Description */
      description: string;
      /** Sound */
      sound: string;
      /** Overhead */
      overhead: string;
    };
    /** DirectionRevisionInput */
    DirectionRevisionInput: {
      /** Expected Revision */
      expected_revision: number;
    };
    /** DirectionSegmentPlanDTO */
    DirectionSegmentPlanDTO: {
      /** Segment Id */
      segment_id: string;
      /** Cuts */
      cuts: components["schemas"]["CutPlanDTO"][];
      /** Technique Id */
      technique_id: string;
      /** Title */
      title: string;
      /** Ready */
      ready: boolean;
      /** Refusals */
      refusals: components["schemas"]["DirectionSegmentRefusalDTO"][];
      /** Sheet Asset */
      sheet_asset: string | null;
      opening?: components["schemas"]["OpeningPlanDTO"] | null;
      place?: components["schemas"]["PlaceStatusDTO"] | null;
      seed?: components["schemas"]["SeedStateDTO"] | null;
      /** Build Refusal */
      build_refusal?: string | null;
    };
    /** DirectionSegmentRefusalDTO */
    DirectionSegmentRefusalDTO: {
      /** Code */
      code: string;
      /** Message */
      message: string;
      /** Cut Ids */
      cut_ids: string[];
      /** Status */
      status: number;
    };
    /** DirectionSegmentSplitCommandDTO */
    DirectionSegmentSplitCommandDTO: {
      /** Segments */
      segments: components["schemas"]["SegmentDTO"][];
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** DirectionSegmentSplitInput */
    DirectionSegmentSplitInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Frame */
      frame: number;
    };
    /** DirectionSplitPointDTO */
    DirectionSplitPointDTO: {
      /** Frame */
      frame: number;
      /** Code */
      code:
        | (
            | "cut_has_keyframes"
            | "cut_has_suitability_history"
            | "missing_initial_phase"
          )
        | null;
      /** Reason */
      reason: string | null;
    };
    /** DirectionSplitRefusalDTO */
    DirectionSplitRefusalDTO: {
      /**
       * Code
       * @enum {string}
       */
      code:
        | "not_a_boundary"
        | "cut_has_keyframes"
        | "cut_has_suitability_history"
        | "missing_initial_phase";
      /** Detail */
      detail: string;
    };
    /** EmptyPlateInput */
    EmptyPlateInput: {
      /** Request Key */
      request_key?: string | null;
      /** Place Id */
      place_id: string;
      /** Angle Id */
      angle_id: string;
      /** Source Asset */
      source_asset: string;
      /**
       * Empty
       * @constant
       */
      empty: true;
    };
    /** EngineReadinessDTO */
    EngineReadinessDTO: {
      /** Name */
      name: string;
      /** Home */
      home: string | null;
      /** Installed */
      installed: boolean;
      /** Worker */
      worker: boolean;
      /** Tasks */
      tasks: string[];
      /** Gpu Tasks */
      gpu_tasks: string[];
      /** Licences */
      licences: string[];
      /** Files */
      files: {
        [key: string]: number;
      } | null;
      /** Verified At */
      verified_at: string | null;
      /** Probe */
      probe: string | null;
      /** Python */
      python: string | null;
      /** Cuda Device */
      cuda_device: string | null;
      /** Drift */
      drift: string[];
      /** Probed At */
      probed_at: string | null;
    };
    /** EnginesDTO */
    EnginesDTO: {
      /** Root */
      root: string | null;
      /**
       * Root Source
       * @enum {string}
       */
      root_source: "environment" | "setting" | "none";
      /** Engines */
      engines: components["schemas"]["EngineReadinessDTO"][];
    };
    /** EntityInput */
    EntityInput: {
      /** Name */
      name: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "character" | "outfit" | "environment";
      /**
       * Description
       * @default
       */
      description: string;
    };
    /** EstimateDTO */
    EstimateDTO: {
      /** Shots */
      shots: number;
      /** Windows */
      windows: number;
      /**
       * Windows Pending
       * @default 0
       */
      windows_pending: number;
      /** Renders */
      renders: number;
      /** Gpu Seconds Low */
      gpu_seconds_low: number;
      /** Gpu Seconds High */
      gpu_seconds_high: number;
      /** Storage Bytes */
      storage_bytes: number;
      /**
       * Basis
       * @enum {string}
       */
      basis: "seed" | "measured";
    };
    /** EventDTO */
    EventDTO: {
      /** At Frame */
      at_frame: number;
      /** Description */
      description: string;
    };
    /** EvidenceDTO */
    EvidenceDTO: {
      /** Id */
      id: string;
      /** Angle Id */
      angle_id: string;
      /** Kind */
      kind: string;
      /** Asset Id */
      asset_id: string;
      /** Approved */
      approved: boolean;
      /** Approved At */
      approved_at: string | null;
      /** Approved By */
      approved_by: string | null;
      /** Inputs */
      inputs: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
    };
    /**
     * FaceDTO
     * @description A face crop from the film (Library artifact rendition), for cards and trays.
     */
    FaceDTO: {
      /** Artifact Id */
      artifact_id: string;
      /** Url */
      url: string;
      /** Frame */
      frame: number;
      /** Track Id */
      track_id?: string | null;
    };
    /** FilmDTO */
    FilmDTO: {
      /** Asset Id */
      asset_id: string;
      /** Name */
      name: string;
      /** Fps Num */
      fps_num: number;
      /** Fps Den */
      fps_den: number;
      /** Frame Count */
      frame_count: number;
      /** Width */
      width: number;
      /** Height */
      height: number;
      /** Has Audio */
      has_audio: boolean;
      /**
       * Fps Status
       * @enum {string}
       */
      fps_status: "tested" | "developing" | "experimental" | "unsupported";
      /** Letterbox */
      letterbox: number[] | null;
    };
    /**
     * FrameRangeDTO
     * @description Film frames, start inclusive, end exclusive.
     */
    FrameRangeDTO: {
      /** Start */
      start: number;
      /** End */
      end: number;
    };
    /** GapDTO */
    GapDTO: {
      /** Code */
      code: string;
      /** Message */
      message: string;
      /** Actions */
      actions: components["schemas"]["NextActionDTO"][];
    };
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components["schemas"]["ValidationError"][];
    };
    /** HarnessSessionDTO */
    HarnessSessionDTO: {
      /** Id */
      id: string;
      /** Movie Id */
      movie_id: string;
      /** Scope Kind */
      scope_kind: string;
      /** Scope Id */
      scope_id: string;
      /** State */
      state: string;
      /** Revision */
      revision: number;
      board: components["schemas"]["Whiteboard"];
      budgets: components["schemas"]["AgentBudgets"];
      /** Stop Reason */
      stop_reason: string;
      /** Waiting On */
      waiting_on: string;
      /** Jobs Reserved */
      jobs_reserved: number;
      /** Jobs Limit */
      jobs_limit: number;
      /**
       * Autonomy
       * @enum {string}
       */
      autonomy: "off" | "prepare" | "render";
      /** Plan Mode */
      plan_mode: boolean;
      plan: components["schemas"]["AgentPlanDTO"] | null;
      /** Plan History */
      plan_history: components["schemas"]["AgentPlanDTO"][];
      /** Activity */
      activity: string;
      /** Summary */
      summary: string;
      /** Plan Jobs Max */
      plan_jobs_max: number;
      build?: components["schemas"]["AgentBuildDTO"] | null;
    };
    /** HeadPassInput */
    HeadPassInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Version */
      version: string;
      /** Character Id */
      character_id: string;
    };
    /** HealthDTO */
    HealthDTO: {
      /** Status */
      status: string;
      /** Version */
      version: string;
    };
    /** ImportDTO */
    ImportDTO: {
      asset: components["schemas"]["AssetDTO"];
      /**
       * Disposition
       * @enum {string}
       */
      disposition: "created" | "reused";
    };
    /** InsertionInput */
    InsertionInput: {
      /** Request Key */
      request_key?: string | null;
      /** Cut Id */
      cut_id: string;
      /** Evidence Id */
      evidence_id: string;
      /** Subject Asset */
      subject_asset: string;
      /** Wording */
      wording: {
        [key: string]: unknown;
      };
      /** Seeds */
      seeds: number[];
    };
    /** JobDTO */
    JobDTO: {
      /** Ordinal */
      ordinal: number;
      /** Kind */
      kind: string;
      /** Executor */
      executor: string;
      /** Integration */
      integration: string;
      /** Step */
      step: string | null;
      /** State */
      state: string;
      /** Seed */
      seed: number | null;
      /** Error */
      error: string | null;
    };
    /** KeyframeDTO */
    KeyframeDTO: {
      /** Subject Asset Id */
      subject_asset_id: string | null;
      /** Id */
      id: string;
      /** Cut Id */
      cut_id: string;
      /** Asset Id */
      asset_id: string;
      /** Evidence Id */
      evidence_id: string;
      /** Approved */
      approved: boolean;
      /** Approved At */
      approved_at: string | null;
      /** Approved By */
      approved_by: string | null;
      /** Inputs */
      inputs: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
    };
    /** LaterPhaseDTO */
    LaterPhaseDTO: {
      /** Description */
      description: string;
      /** Screen Direction */
      screen_direction: string;
      /** Sound */
      sound: string;
      /** Start Frame */
      start_frame: number;
    };
    /** LibraryDTO */
    LibraryDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Created At */
      created_at: string;
    };
    /** LibraryState */
    LibraryState: {
      library: components["schemas"]["LibraryDTO"] | null;
      archived: components["schemas"]["ArchivedLibraryDTO"] | null;
    };
    /** LineCountsDTO */
    LineCountsDTO: {
      /** Segments */
      segments: number;
      /** Proposed */
      proposed: number;
      /** Confirmed */
      confirmed: number;
      /** Rejected */
      rejected: number;
    };
    /**
     * LineDTO
     * @description One transcript segment as a line: its decision, or the rule's computed proposal (48 kHz samples).
     */
    LineDTO: {
      /** Segment Key */
      segment_key: string;
      /** Index */
      index: number;
      /** Shot Id */
      shot_id: string | null;
      /** Start Sample */
      start_sample: number;
      /** End Sample */
      end_sample: number;
      /** Start Frame */
      start_frame: number;
      /** End Frame */
      end_frame: number;
      /** Text */
      text: string;
      /** Words */
      words: components["schemas"]["LineWordDTO"][];
      /** Character Id */
      character_id: string | null;
      /** State */
      state: ("proposed" | "confirmed" | "rejected") | null;
      /** Proposed By */
      proposed_by: ("analysis" | "agent" | "user") | null;
      /**
       * Edges
       * @enum {string}
       */
      edges: "rule" | "user";
      /** Whispered */
      whispered: boolean;
      /** Median Hz */
      median_hz: number | null;
      /** Evidence */
      evidence?: {
        [key: string]: unknown;
      };
      /** Decided At */
      decided_at: string | null;
    };
    /** LineDecisionInput */
    LineDecisionInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Character Id */
      character_id: string;
    };
    /** LineEdgesInput */
    LineEdgesInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Start Sample */
      start_sample?: number | null;
      /** End Sample */
      end_sample?: number | null;
    };
    /** LineProfileDTO */
    LineProfileDTO: {
      /** Segments */
      segments: number;
      /** Seconds */
      seconds: number;
    };
    /** LineWordDTO */
    LineWordDTO: {
      /** Start Sample */
      start_sample: number;
      /** End Sample */
      end_sample: number;
      /** Word */
      word: string;
    };
    /** LinesDTO */
    LinesDTO: {
      /** Dialogue Operation Id */
      dialogue_operation_id: string | null;
      /** Rule Version */
      rule_version: number;
      /** Sample Rate */
      sample_rate: number;
      /** Lines */
      lines: components["schemas"]["LineDTO"][];
      /** Profiles */
      profiles: {
        [key: string]: components["schemas"]["LineProfileDTO"];
      };
    };
    /** MergeInput */
    MergeInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Into */
      into: string;
      /** Sources */
      sources: string[];
    };
    /** MessageInput */
    MessageInput: {
      /** Text */
      text: string;
      /** Request Key */
      request_key: string;
      /** Attachments */
      attachments?: string[];
    };
    /** MoveTracksInput */
    MoveTracksInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Track Ids */
      track_ids: string[];
      /** To */
      to: string;
    };
    /** MovieCommandDTO */
    MovieCommandDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Title */
      title: string;
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** MovieDetailDTO */
    MovieDetailDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Title */
      title: string;
      /** Subjects */
      subjects: components["schemas"]["SubjectDTO"][];
      /** Segments */
      segments: components["schemas"]["SegmentDTO"][];
    };
    /** MovieExportDTO */
    MovieExportDTO: {
      /** Movie Id */
      movie_id: string;
      /** Movie Revision */
      movie_revision: number;
      /** Title */
      title: string;
      /** Segments */
      segments: components["schemas"]["MovieExportSegmentDTO"][];
      latest: components["schemas"]["MovieExportStatusDTO"] | null;
    };
    /** MovieExportInput */
    MovieExportInput: {
      /** Request Key */
      request_key: string;
      /**
       * Allow Stale
       * @default false
       */
      allow_stale: boolean;
    };
    /** MovieExportSegmentDTO */
    MovieExportSegmentDTO: {
      /** Segment Id */
      segment_id: string;
      /** Ordinal */
      ordinal: number;
      /** Frames */
      frames: number;
      /** Selection Id */
      selection_id: string | null;
      /** Asset Id */
      asset_id: string | null;
      /** Name */
      name: string | null;
      /** Stale Reasons */
      stale_reasons: string[];
    };
    /** MovieExportStatusDTO */
    MovieExportStatusDTO: {
      /** Id */
      id: string;
      /** State */
      state: string;
      /** Progress */
      progress: number;
      /** Asset Id */
      asset_id: string | null;
      /** Cancel Requested */
      cancel_requested: boolean;
      /** Error */
      error: string | null;
    };
    /** MovieInput */
    MovieInput: {
      /** Title */
      title: string;
    };
    /** MoviePatch */
    MoviePatch: {
      /** Expected Revision */
      expected_revision: number;
      /** Title */
      title: string;
    };
    /** NameInput */
    NameInput: {
      /** Name */
      name: string;
    };
    /** NamedInput */
    NamedInput: {
      /** Name */
      name: string;
    };
    /** NextActionDTO */
    NextActionDTO: {
      action: components["schemas"]["Action"];
      /** Target */
      target: string;
      /** Reason */
      reason: string;
    };
    /** NotSameInput */
    NotSameInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Pairs */
      pairs: components["schemas"]["NotSamePairDTO"][];
    };
    /** NotSamePairDTO */
    NotSamePairDTO: {
      /** Character A */
      character_a: string;
      /** Character B */
      character_b: string;
    };
    /**
     * OpeningPlanDTO
     * @description The plan of a segment's first shot (ADR 0019 §1).
     */
    OpeningPlanDTO: {
      /** Bindings */
      bindings: components["schemas"]["CutBindingDTO"][];
      /** Segment Id */
      segment_id: string;
      /** Angle Id */
      angle_id: string;
      assessment: components["schemas"]["AssessmentDTO"];
      recommended: components["schemas"]["ChoiceDTO"] | null;
      /** Alternatives */
      alternatives: components["schemas"]["ChoiceDTO"][];
      /** Unavailable */
      unavailable: [string, string][];
      /** Gaps */
      gaps: components["schemas"]["GapDTO"][];
      /** Override */
      override: boolean;
    };
    /** OperationDTO */
    OperationDTO: {
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      /** State */
      state: string;
      /** Awaits Review */
      awaits_review: boolean;
      /** Request */
      request: {
        [key: string]: unknown;
      };
      /** Budget Reserved */
      budget_reserved: number;
      /** Budget Released At */
      budget_released_at: string | null;
      /** Cancel Requested */
      cancel_requested: boolean;
      /** Review */
      review: unknown[] | null;
      /** Error */
      error: string | null;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Jobs */
      jobs: components["schemas"]["JobDTO"][];
      /** Candidates */
      candidates: components["schemas"]["CandidateDTO"][];
    };
    /**
     * OperationSummaryDTO
     * @description Enough to route a review link without loading candidate provenance into the UI.
     */
    OperationSummaryDTO: {
      /** Id */
      id: string;
      /** Kind */
      kind: string;
    };
    /** PhaseCommandDTO */
    PhaseCommandDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Segment Id */
      segment_id: string;
      /** Start Frame */
      start_frame: number;
      content: components["schemas"]["ShotDTO"];
      /** Content Schema */
      content_schema: number;
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** PhaseCreate */
    PhaseCreate: {
      /** Expected Revision */
      expected_revision: number;
      /** Start Frame */
      start_frame: number;
      content: components["schemas"]["ShotDTO"];
    };
    /** PhaseDTO */
    PhaseDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Segment Id */
      segment_id: string;
      /** Start Frame */
      start_frame: number;
      content: components["schemas"]["ShotDTO"];
      /** Content Schema */
      content_schema: number;
    };
    /** PhaseInput */
    PhaseInput: {
      /** Start Frame */
      start_frame: number;
      content: components["schemas"]["ShotDTO"];
    };
    /** PhasePatch */
    PhasePatch: {
      /** Expected Revision */
      expected_revision: number;
      /** Start Frame */
      start_frame?: number | null;
      content?: components["schemas"]["ShotDTO"] | null;
    };
    /** PinApprovalInput */
    PinApprovalInput: {
      /** Candidate Id */
      candidate_id: string;
      /**
       * Replace
       * @default false
       */
      replace: boolean;
    };
    /** PlaceDTO */
    PlaceDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Sound */
      sound: string;
      /** Overhead */
      overhead: string;
      /** Revision */
      revision: number;
      /** Plate Asset Id */
      plate_asset_id: string | null;
      /** Plate Angle Id */
      plate_angle_id: string | null;
      /** Set Id */
      set_id?: string | null;
      /** Created At */
      created_at: string;
      /**
       * View Producer
       * @description Who makes this place's reviewed angle views (application/prepare_references.view_producer).
       * @enum {string}
       */
      readonly view_producer: "survey" | "set_views";
    };
    /**
     * PlaceSetInput
     * @description The set this place is built as (ADR 0019 §1), or null to unlink it.
     */
    PlaceSetInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Set Id */
      set_id?: string | null;
    };
    /**
     * PlaceStatusDTO
     * @description What a segment's first shot takes from its place, in words (ADR 0019 §5).
     */
    PlaceStatusDTO: {
      /** Place Id */
      place_id: string;
      /** Place Name */
      place_name: string;
      /** Set Id */
      set_id: string | null;
      /** Set Name */
      set_name: string | null;
      /** Set Version */
      set_version: number | null;
      /** Uses Views */
      uses_views: boolean;
      /** Uses */
      uses: string;
      /** Rendered Version */
      rendered_version: number | null;
      /** Update Available */
      update_available: boolean;
      /** Blocker */
      blocker: string | null;
    };
    /** PlaceTextDTO */
    PlaceTextDTO: {
      /** Noun */
      noun: string;
      /** Description */
      description: string;
      /** Landmarks */
      landmarks: string;
    };
    /** PlanInput */
    PlanInput: {
      /** Expected Revision */
      expected_revision: number;
      range?: components["schemas"]["FrameRangeDTO"] | null;
    };
    /** PrescreenCheckDTO */
    PrescreenCheckDTO: {
      /** Check */
      check: string;
      /**
       * Verdict
       * @enum {string}
       */
      verdict: "pass" | "fail" | "unsupported";
      /** Evidence */
      evidence: {
        [key: string]: unknown;
      };
    };
    /** PrescreenDTO */
    PrescreenDTO: {
      /** Version */
      version: number;
      /** Measured At */
      measured_at: string;
      /** Checks */
      checks: components["schemas"]["PrescreenCheckDTO"][];
    };
    /** ProductionEvidenceDTO */
    ProductionEvidenceDTO: {
      /** Id */
      id: string;
      /** Angle Id */
      angle_id: string;
      /** Kind */
      kind: string;
      /** Asset Id */
      asset_id: string;
      /** Approved */
      approved: boolean;
      /** Approved At */
      approved_at: string | null;
      /** Approved By */
      approved_by: string | null;
      /** Inputs */
      inputs: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
    };
    /** ProductionKeyframeDTO */
    ProductionKeyframeDTO: {
      receipt?: components["schemas"]["ReceiptDTO"] | null;
      /** Subject Asset Id */
      subject_asset_id: string | null;
      /** Id */
      id: string;
      /** Cut Id */
      cut_id: string;
      /** Asset Id */
      asset_id: string;
      /** Evidence Id */
      evidence_id: string;
      /** Approved */
      approved: boolean;
      /** Approved At */
      approved_at: string | null;
      /** Approved By */
      approved_by: string | null;
      /** Inputs */
      inputs: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
    };
    /** ProductionReferenceRejectionDTO */
    ProductionReferenceRejectionDTO: {
      /**
       * Rejected By
       * @constant
       */
      rejected_by: "user";
      /** Rejected At */
      rejected_at: string;
      /** Note */
      note: string;
      /** Candidate Ids */
      candidate_ids: string[];
    };
    /** ProductionReferenceRejectionInput */
    ProductionReferenceRejectionInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Note */
      note: string;
    };
    /**
     * ProductionSheetTilesDTO
     * @description Default inputs for a new assembly; existing layouts have their own identity.
     */
    ProductionSheetTilesDTO: {
      /** Place Id */
      place_id: string;
      /** Tiles */
      tiles: (string | [number, number])[];
    };
    /** RankingDTO */
    RankingDTO: {
      /** Review Set Id */
      review_set_id: string;
      /** Tiers */
      tiers: string[][];
      /** Usable Tiers */
      usable_tiers: number;
      /** Notes */
      notes: {
        [key: string]: string;
      };
      /** Tags */
      tags: {
        [key: string]: string[];
      };
      /** General */
      general: string;
      /** Revision */
      revision: number;
      /** Updated At */
      updated_at: string;
    };
    /** RankingInput */
    RankingInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Tiers */
      tiers: string[][];
      /** Usable Tiers */
      usable_tiers: number;
      /** Notes */
      notes?: {
        [key: string]: string;
      };
      /** Tags */
      tags?: {
        [key: string]: string[];
      };
      /**
       * General
       * @default
       */
      general: string;
    };
    /** RecastDTO */
    RecastDTO: {
      /** Id */
      id: string;
      /** Title */
      title: string;
      /** Revision */
      revision: number;
      film: components["schemas"]["FilmDTO"];
      analysis: components["schemas"]["AnalysisStatusDTO"];
      dialogue: components["schemas"]["DialogueStatusDTO"];
      lines: components["schemas"]["LineCountsDTO"];
      /** Characters */
      characters: components["schemas"]["CharacterDTO"][];
      /** Unknown Faces */
      unknown_faces: components["schemas"]["FaceDTO"][];
      /**
       * Not Same
       * @default []
       */
      not_same: components["schemas"]["NotSamePairDTO"][];
      /** Edits */
      edits?: components["schemas"]["RecastEditDTO"][];
      /** Shots */
      shots: components["schemas"]["RecastShotDTO"][];
      run: components["schemas"]["RunDTO"];
      estimate: components["schemas"]["EstimateDTO"] | null;
      /** Conforms */
      conforms: components["schemas"]["ConformDTO"][];
    };
    /**
     * RecastEditDTO
     * @description One journaled cast edit (ADR 0012 Amendment 7 §1); its row images never leave the backend.
     */
    RecastEditDTO: {
      /** Id */
      id: string;
      /** Command */
      command: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "edit" | "undo" | "redo";
      /** Summary */
      summary: string;
      /** Created At */
      created_at: string;
      /** Undone At */
      undone_at: string | null;
    };
    /** RecastGapDTO */
    RecastGapDTO: {
      /** Code */
      code: string;
      /** Message */
      message: string;
    };
    /** RecastShotDTO */
    RecastShotDTO: {
      /** Appearance Links */
      appearance_links?: components["schemas"]["AppearanceLinkDTO"][];
      /** Layers */
      layers?: components["schemas"]["ShotLayerDTO"][];
      /**
       * Retaking
       * @default false
       */
      retaking: boolean;
      /** Id */
      id: string;
      /** Index */
      index: number;
      /** Start */
      start: number;
      /** End */
      end: number;
      poster: components["schemas"]["FaceDTO"] | null;
      /** Appearances */
      appearances: components["schemas"]["AppearanceDTO"][];
      /** Included */
      included: boolean;
      /** Priority */
      priority: number | null;
      plan: components["schemas"]["ShotPlanDTO"] | null;
      /**
       * Status
       * @enum {string}
       */
      status:
        | "absent"
        | "excluded"
        | "gap"
        | "unplanned"
        | "analysing"
        | "planned"
        | "queued"
        | "rendering"
        | "needs_review"
        | "approved"
        | "kept_original"
        | "failed";
      /** Versions */
      versions: components["schemas"]["ShotVersionDTO"][];
      choice: components["schemas"]["ShotChoiceDTO"] | null;
    };
    /** RecastSummaryDTO */
    RecastSummaryDTO: {
      /** Id */
      id: string;
      /** Title */
      title: string;
      /** Source Asset Id */
      source_asset_id: string;
      /** Revision */
      revision: number;
      /** Analysis State */
      analysis_state: string;
      /** Shots Total */
      shots_total: number;
      /** Shots Approved */
      shots_approved: number;
      /** Updated At */
      updated_at: string;
    };
    /** ReceiptDTO */
    ReceiptDTO: {
      /** Id */
      id: string;
      /** Movie Id */
      movie_id: string;
      /** Command */
      command: string;
      /**
       * Target Kind
       * @enum {string}
       */
      target_kind:
        "movie" | "subject" | "segment" | "phase" | "cut" | "angle" | "place";
      /** Target Id */
      target_id: string;
      /** Before */
      before: {
        [key: string]: unknown;
      };
      /** After */
      after: {
        [key: string]: unknown;
      };
      /** Undoes */
      undoes: string | null;
      /** Undone At */
      undone_at: string | null;
      /** Created At */
      created_at: string;
    };
    /** ReferenceDTO */
    ReferenceDTO: {
      /** Asset Id */
      asset_id: string | null;
      /**
       * State
       * @enum {string}
       */
      state: "making" | "waiting" | "approved" | "rejected" | "failed";
    };
    /** RemovalInput */
    RemovalInput: {
      /** Request Key */
      request_key?: string | null;
      /** Place Id */
      place_id: string;
      /** Angle Id */
      angle_id: string;
      /** Source Asset */
      source_asset: string;
      /** Frame */
      frame?: number | null;
      /** Wording */
      wording: {
        [key: string]: unknown;
      };
      /** Seeds */
      seeds: number[];
    };
    /** RemovedDTO */
    RemovedDTO: {
      /** Id */
      id: string;
      /** Removed */
      removed: boolean;
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** RenderInput */
    RenderInput: {
      /** Request Key */
      request_key?: string | null;
      /** Segment Id */
      segment_id?: string | null;
      /** Cut Id */
      cut_id?: string | null;
      /** Seeds */
      seeds: number[];
      /** Sheet Asset */
      sheet_asset?: string | null;
      /** Context Artifact */
      context_artifact?: string | null;
    };
    /** ReplacementAnalysisPointDTO */
    ReplacementAnalysisPointDTO: {
      /** X */
      x: number;
      /** Y */
      y: number;
    };
    /** ReplacementAnalysisPointsDTO */
    ReplacementAnalysisPointsDTO: {
      /** Positive */
      positive: components["schemas"]["ReplacementAnalysisPointDTO"][];
      /** Negative */
      negative?: components["schemas"]["ReplacementAnalysisPointDTO"][];
    };
    /** ReplacementApprovalDTO */
    ReplacementApprovalDTO: {
      /** Asset Id */
      asset_id: string;
      /** Provenance */
      provenance: {
        [key: string]: unknown;
      };
    };
    /** ReplacementArmDTO */
    ReplacementArmDTO: {
      /**
       * Treatment
       * @enum {string}
       */
      treatment: "rgb" | "inverted";
      /**
       * Grouping
       * @enum {string}
       */
      grouping: "single" | "joint";
      /**
       * Framing
       * @enum {string}
       */
      framing: "crop" | "pair" | "active" | "full" | "head";
      crop: components["schemas"]["ReplacementCropDTO"] | null;
      /** Seed Count */
      seed_count: number;
      /** Round */
      round: number;
      /** Seed Offset */
      seed_offset: number;
    };
    /** ReplacementBoxDTO */
    ReplacementBoxDTO: {
      /** X0 */
      x0: number;
      /** Y0 */
      y0: number;
      /** X1 */
      x1: number;
      /** Y1 */
      y1: number;
    };
    /** ReplacementBystanderDTO */
    ReplacementBystanderDTO: {
      /** Track Id */
      track_id: string;
      /** Role */
      role: string;
      /** Position */
      position: string;
      /** Items */
      items: string[];
    };
    /** ReplacementCheckDTO */
    ReplacementCheckDTO: {
      /** Calibrated */
      calibrated: boolean;
      /** Check */
      check: string;
      /**
       * Verdict
       * @enum {string}
       */
      verdict: "pass" | "fail" | "unsupported";
      /** Measured */
      measured: number | null;
      /** Advice */
      advice: string;
      /** Reason */
      reason: string;
    };
    /** ReplacementCropDTO */
    ReplacementCropDTO: {
      /** Canvas */
      canvas: [number, number];
      box: components["schemas"]["ReplacementBoxDTO"];
      /** Scale */
      scale: number;
      /** Interior Edges */
      interior_edges: ("left" | "right" | "top" | "bottom")[];
      /** Feather */
      feather: number;
      /** Clearance */
      clearance: number | null;
      /** Matrix */
      matrix: [[number, number, number], [number, number, number]];
    };
    /** ReplacementFactsDTO */
    ReplacementFactsDTO: {
      /** Source Asset */
      source_asset: string;
      /** Shot Start */
      shot_start: number;
      /** Shot End */
      shot_end: number;
      /** Native */
      native: [number, number];
      /** Fps */
      fps: string;
      letterbox: components["schemas"]["ReplacementLetterboxDTO"] | null;
      /** Performers */
      performers: components["schemas"]["ReplacementPerformerDTO"][];
      /** Bystanders */
      bystanders: components["schemas"]["ReplacementBystanderDTO"][];
      /** Occluders */
      occluders: components["schemas"]["ReplacementOccluderInput"][];
      /** Scene Items */
      scene_items: string[];
      /** Quiet */
      quiet: boolean;
      /** Silent Intervals */
      silent_intervals: [number, number][];
      /** Tracks */
      tracks: components["schemas"]["ReplacementTrackDTO"][];
      /** Performer Hands Visible */
      performer_hands_visible: boolean | null;
      /** Source Target Similarity */
      source_target_similarity: number | null;
      /** Required Context */
      required_context: components["schemas"]["ReplacementBoxDTO"][];
      /** Avoided Regions */
      avoided_regions: components["schemas"]["ReplacementBoxDTO"][];
    };
    /** ReplacementGapDTO */
    ReplacementGapDTO: {
      /** Code */
      code: string;
      /** Message */
      message: string;
    };
    /** ReplacementLetterboxDTO */
    ReplacementLetterboxDTO: {
      /** Fit */
      fit: [number, number];
      /** Offset */
      offset: [number, number];
      /** Canvas */
      canvas: [number, number];
    };
    /** ReplacementOccluderInput */
    ReplacementOccluderInput: {
      /** Name */
      name: string;
      /**
       * Owner
       * @enum {string}
       */
      owner: "scene" | "performer";
      /** Covers */
      covers: string;
    };
    /** ReplacementPeopleAnalysisDTO */
    ReplacementPeopleAnalysisDTO: {
      /** Operation Id */
      operation_id: string;
      /** State */
      state: string;
      /** Report */
      report: {
        [key: string]: unknown;
      } | null;
    };
    /** ReplacementPeopleAnalysisInput */
    ReplacementPeopleAnalysisInput: {
      /** Source Asset Id */
      source_asset_id: string;
      /** Start */
      start: number;
      /** End */
      end: number;
      /**
       * Concepts
       * @default [
       *       "person"
       *     ]
       */
      concepts: string[];
      /** Points */
      points?: {
        [key: string]: components["schemas"]["ReplacementAnalysisPointsDTO"];
      } | null;
      /** Request Key */
      request_key?: string | null;
    };
    /** ReplacementPerformerDTO */
    ReplacementPerformerDTO: {
      /** Track Id */
      track_id: string;
      /** Target Asset */
      target_asset: string;
      /** Target Subject Id */
      target_subject_id: string | null;
      /** Target Text */
      target_text: string | null;
      /** Role */
      role: string;
      /** Position */
      position: string;
      /** Action */
      action: string | null;
    };
    /** ReplacementPerformerInput */
    ReplacementPerformerInput: {
      /** Track Id */
      track_id: string;
      /** Target Asset */
      target_asset: string;
      /** Role */
      role?: string;
      /** Position */
      position?: string | null;
      /** Target Subject Id */
      target_subject_id?: string | null;
      /** Action */
      action?: string | null;
      /**
       * Selected By
       * @default user
       * @enum {string}
       */
      selected_by: "user" | "identity";
    };
    /** ReplacementPlanDTO */
    ReplacementPlanDTO: {
      arm: components["schemas"]["ReplacementArmDTO"] | null;
      /** Gaps */
      gaps: components["schemas"]["ReplacementGapDTO"][];
      /** Reasons */
      reasons: string[];
      /** Stop */
      stop: boolean;
      /** Technique Ids */
      technique_ids: string[];
      facts: components["schemas"]["ReplacementFactsDTO"];
      crop_preview: components["schemas"]["ReplacementCropDTO"] | null;
    };
    /** ReplacementPlanInput */
    ReplacementPlanInput: {
      /** Renders */
      renders?: number | null;
      /** Source Asset */
      source_asset: string;
      /** Analysis Operation */
      analysis_operation: string;
      /** Performers */
      performers: components["schemas"]["ReplacementPerformerInput"][];
      /**
       * Seed
       * @default 0
       */
      seed: number;
      /** Required Context */
      required_context?: components["schemas"]["ReplacementBoxDTO"][];
      /** Avoided Regions */
      avoided_regions?: components["schemas"]["ReplacementBoxDTO"][];
      /**
       * Quiet
       * @default false
       */
      quiet: boolean;
      /** Scene Items */
      scene_items?: string[];
      /** Occluders */
      occluders?: components["schemas"]["ReplacementOccluderInput"][];
      /** Silent Intervals */
      silent_intervals?: [number, number][];
      /** Performer Hands Visible */
      performer_hands_visible?: boolean | null;
      /** Source Target Similarity */
      source_target_similarity?: number | null;
    };
    /** ReplacementReviewArmDTO */
    ReplacementReviewArmDTO: {
      /** Treatment */
      treatment: string;
      /** Framing */
      framing: string;
    };
    /** ReplacementStartInput */
    ReplacementStartInput: {
      /** Renders */
      renders?: number | null;
      /** Source Asset */
      source_asset: string;
      /** Analysis Operation */
      analysis_operation: string;
      /** Performers */
      performers: components["schemas"]["ReplacementPerformerInput"][];
      /**
       * Seed
       * @default 0
       */
      seed: number;
      /** Required Context */
      required_context?: components["schemas"]["ReplacementBoxDTO"][];
      /** Avoided Regions */
      avoided_regions?: components["schemas"]["ReplacementBoxDTO"][];
      /**
       * Quiet
       * @default false
       */
      quiet: boolean;
      /** Scene Items */
      scene_items?: string[];
      /** Occluders */
      occluders?: components["schemas"]["ReplacementOccluderInput"][];
      /** Silent Intervals */
      silent_intervals?: [number, number][];
      /** Performer Hands Visible */
      performer_hands_visible?: boolean | null;
      /** Source Target Similarity */
      source_target_similarity?: number | null;
      /** Request Key */
      request_key?: string | null;
      /** Budget Limit */
      budget_limit?: number | null;
    };
    /** ReplacementTrackDTO */
    ReplacementTrackDTO: {
      /** Id */
      id: string;
      box: components["schemas"]["ReplacementBoxDTO"];
      /** Presence */
      presence: ("present" | "absent" | "unresolved")[];
      /** Identity Margin */
      identity_margin: number | null;
      /**
       * Selected By
       * @enum {string}
       */
      selected_by: "user" | "identity";
    };
    /** RetakeInput */
    RetakeInput: {
      /** Expected Revision */
      expected_revision: number;
      /**
       * Mode
       * @default draft
       * @enum {string}
       */
      mode: "draft" | "final";
    };
    /** ReviewDTO */
    ReviewDTO: {
      /** Id */
      id: string;
      /** Purpose */
      purpose: string;
      /** Context */
      context: {
        [key: string]: unknown;
      };
      /** Revision */
      revision: number;
      /** Members */
      members: components["schemas"]["ReviewMemberDTO"][];
      /** Unranked */
      unranked: string[];
      ranking: components["schemas"]["RankingDTO"];
      rejection?:
        components["schemas"]["ProductionReferenceRejectionDTO"] | null;
    };
    /** ReviewInput */
    ReviewInput: {
      /**
       * Review
       * @enum {string}
       */
      review: "unreviewed" | "preferred" | "rejected";
    };
    /** ReviewMemberDTO */
    ReviewMemberDTO: {
      /** Replacement Checks */
      replacement_checks?:
        components["schemas"]["ReplacementCheckDTO"][] | null;
      /** Round */
      round?: number | null;
      arm?: components["schemas"]["ReplacementReviewArmDTO"] | null;
      prescreen?: components["schemas"]["PrescreenDTO"] | null;
      /** Letter */
      letter: string;
      /** Candidate Id */
      candidate_id: string;
      /** Asset Id */
      asset_id: string;
      /** Kind */
      kind: string;
      /** Job Id */
      job_id?: string | null;
      /** Operation Id */
      operation_id?: string | null;
      /** Integration */
      integration?: string | null;
      /** Seed */
      seed?: number | null;
      /** Graph */
      graph?: {
        [key: string]: unknown;
      } | null;
      /** Request */
      request?: {
        [key: string]: unknown;
      } | null;
    };
    /** ReviewTagDTO */
    ReviewTagDTO: {
      /** Id */
      id: string;
      /** Label */
      label: string;
      /** Applies To */
      applies_to: string[];
    };
    /** ReviewTaxonomyDTO */
    ReviewTaxonomyDTO: {
      /** Schema */
      schema: number;
      /** Tags */
      tags: components["schemas"]["ReviewTagDTO"][];
    };
    /** RevisionInputDTO */
    RevisionInputDTO: {
      /** Expected Revision */
      expected_revision: number;
    };
    /** RunDTO */
    RunDTO: {
      /** Reason */
      reason?: string | null;
      /**
       * State
       * @enum {string}
       */
      state: "idle" | "running" | "paused" | "finished";
      /**
       * Mode
       * @enum {string}
       */
      mode: "draft" | "final";
      range: components["schemas"]["FrameRangeDTO"] | null;
      /** Render Budget */
      render_budget: number;
      /** Renders Used */
      renders_used: number;
      /** Shots Done */
      shots_done: number;
      /** Shots Total */
      shots_total: number;
      /** Current Shot Id */
      current_shot_id: string | null;
    };
    /** RunInput */
    RunInput: {
      /** Expected Revision */
      expected_revision: number;
      /**
       * Mode
       * @default draft
       * @enum {string}
       */
      mode: "draft" | "final";
      range?: components["schemas"]["FrameRangeDTO"] | null;
      /** Render Budget */
      render_budget: number;
    };
    /** SaveCredential */
    SaveCredential: {
      /** Id */
      id: string;
      /**
       * Value
       * Format: password
       */
      value: string;
      /**
       * Storage
       * @default portable
       * @enum {string}
       */
      storage: "portable" | "session";
    };
    /** SaveTelemetry */
    SaveTelemetry: {
      /**
       * Enabled
       * @default false
       */
      enabled: boolean;
      /**
       * Base Url
       * @default https://cloud.langfuse.com
       */
      base_url: string;
      /**
       * Environment
       * @default development
       */
      environment: string;
      /**
       * Storage Limit Gib
       * @default 10
       */
      storage_limit_gib: number;
      /**
       * Credential Storage
       * @default session
       */
      credential_storage: string;
      /** Public Key */
      public_key?: string | null;
      /** Secret Key */
      secret_key?: string | null;
    };
    /** SeedPlaceDTO */
    SeedPlaceDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Set Id */
      set_id: string | null;
    };
    /** SeedShotDTO */
    SeedShotDTO: {
      /** Index */
      index: number;
      /** Label */
      label: string;
      /** Start */
      start: number;
      /** End */
      end: number;
      /** Times */
      times: string;
    };
    /**
     * SeedStateDTO
     * @description A set being built from a take (ADR 0019 §4), in words.
     */
    SeedStateDTO: {
      /**
       * State
       * @enum {string}
       */
      state: "building" | "refused" | "failed" | "done";
      /** Name */
      name: string;
      /** Label */
      label: string;
      /** Message */
      message: string;
      /** Operation Id */
      operation_id: string;
      /** Set Id */
      set_id: string | null;
      /** Shot */
      shot: number;
      /** Waiting */
      waiting: boolean;
      /** Can Retry */
      can_retry: boolean;
    };
    /** SegmentCommandDTO */
    SegmentCommandDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Movie Id */
      movie_id: string;
      /** Ordinal */
      ordinal: number;
      /** Delivered Frames */
      delivered_frames: number;
      /** Continues */
      continues: boolean;
      content: components["schemas"]["SegmentContentDTO"];
      /** Content Schema */
      content_schema: number;
      /** Opening Angle Id */
      opening_angle_id?: string | null;
      /** Opening Technique */
      opening_technique?: string | null;
      /** Phases */
      phases: components["schemas"]["PhaseDTO"][];
      /** Cuts */
      cuts: components["schemas"]["CutDTO"][];
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** SegmentContentDTO */
    SegmentContentDTO: {
      /** Subjects */
      subjects: string[];
      place: components["schemas"]["PlaceTextDTO"];
      /** Style */
      style?: string;
      /** Events */
      events?: components["schemas"]["EventDTO"][];
      /** Speech */
      speech?: components["schemas"]["SpeechDTO"][];
      /** States */
      states?: components["schemas"]["StateDTO"][];
    };
    /** SegmentCreate */
    SegmentCreate: {
      /** Expected Revision */
      expected_revision: number;
      content: components["schemas"]["SegmentContentDTO"];
      /** Delivered Frames */
      delivered_frames: number;
      /** Continues */
      continues: boolean;
      /** Phases */
      phases?: components["schemas"]["PhaseInput"][];
      /** At */
      at?: number | null;
    };
    /** SegmentDTO */
    SegmentDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Movie Id */
      movie_id: string;
      /** Ordinal */
      ordinal: number;
      /** Delivered Frames */
      delivered_frames: number;
      /** Continues */
      continues: boolean;
      content: components["schemas"]["SegmentContentDTO"];
      /** Content Schema */
      content_schema: number;
      /** Opening Angle Id */
      opening_angle_id?: string | null;
      /** Opening Technique */
      opening_technique?: string | null;
      /** Phases */
      phases: components["schemas"]["PhaseDTO"][];
      /** Cuts */
      cuts: components["schemas"]["CutDTO"][];
    };
    /** SegmentMove */
    SegmentMove: {
      /** Expected Revision */
      expected_revision: number;
      /** Ordinal */
      ordinal: number;
    };
    /** SegmentPatch */
    SegmentPatch: {
      /** Expected Revision */
      expected_revision: number;
      content?: components["schemas"]["SegmentContentDTO"] | null;
      /** Delivered Frames */
      delivered_frames?: number | null;
      /** Continues */
      continues?: boolean | null;
      /** Opening Angle Id */
      opening_angle_id?: string | null;
      /** Opening Technique */
      opening_technique?: string | null;
    };
    /** SelectInput */
    SelectInput: {
      /** Candidate Id */
      candidate_id: string;
      /** Expected Revision */
      expected_revision: number;
    };
    /** SelectionDTO */
    SelectionDTO: {
      /**
       * Stale
       * @default false
       */
      stale: boolean;
      /** Stale Reasons */
      stale_reasons?: string[];
      /** Id */
      id: string;
      /** Movie Id */
      movie_id: string;
      /** Segment Id */
      segment_id: string;
      /** Candidate Id */
      candidate_id: string;
      /** Asset Id */
      asset_id: string;
      /** Authority */
      authority: string;
      /** Basis */
      basis: string | null;
      /** Selected At */
      selected_at: string;
      /** Superseded At */
      superseded_at: string | null;
      /** Revision */
      revision: number;
    };
    /** SelectionsDTO */
    SelectionsDTO: {
      current: components["schemas"]["SelectionDTO"] | null;
      /** History */
      history: components["schemas"]["SelectionDTO"][];
      /** Revision */
      revision: number;
    };
    /** SessionDTO */
    SessionDTO: {
      /** Token */
      token: string;
    };
    /** SessionInput */
    SessionInput: {
      /**
       * Movie Id
       * @default
       */
      movie_id: string;
      /**
       * Scope Kind
       * @default movie
       * @enum {string}
       */
      scope_kind: "movie" | "segment" | "cut" | "film";
      /**
       * Scope Id
       * @default
       */
      scope_id: string;
    };
    /** SessionStateDTO */
    SessionStateDTO: {
      session: components["schemas"]["HarnessSessionDTO"];
      /** Turns */
      turns: components["schemas"]["TurnDTO"][];
    };
    /** SetAcceptInput */
    SetAcceptInput: {
      /** Expected Current Id */
      expected_current_id: string | null;
      /** Looks Fixed */
      looks_fixed?: string[];
    };
    /** SetAimInput */
    SetAimInput: {
      /** Version Id */
      version_id: string;
      /** Angle Id */
      angle_id: string | null;
    };
    /**
     * SetBuildDTO
     * @description The set's newest build (ADR 0018 §4): `stage` prepare (the atlas and map), splat (the fit) or, with
     *     extensions, one of a fill round's stages (ADR 0020 §4), which `filling` says in words.
     */
    SetBuildDTO: {
      /** Operation Id */
      operation_id: string;
      /** State */
      state: string;
      /** Base Version Id */
      base_version_id: string | null;
      /** Stage */
      stage: string | null;
      /** Filling */
      filling?: string | null;
      /** Waiting */
      waiting: boolean;
      /** Cancelling */
      cancelling: boolean;
      /** Progress */
      progress: number;
      /** Version Id */
      version_id: string | null;
      /** Error */
      error: string | null;
      /** Reason */
      reason?: string | null;
      /** Fix Ids */
      fix_ids: string[];
    };
    /** SetBuildInput */
    SetBuildInput: {
      /** Version Id */
      version_id: string;
      /**
       * Retry
       * @default false
       */
      retry: boolean;
    };
    /** SetFixDTO */
    SetFixDTO: {
      /** Id */
      id: string;
      /** Issue Id */
      issue_id: string | null;
      /**
       * Action
       * @enum {string}
       */
      action: "reshoot" | "extend" | "retract" | "keep";
      /**
       * Author
       * @enum {string}
       */
      author: "human" | "agent" | "judge";
      /** Target */
      target: {
        [key: string]: unknown;
      };
      /** Shows */
      shows: string;
      /**
       * State
       * @enum {string}
       */
      state: "queued" | "running" | "done" | "failed" | "cancelled";
      /** Operation Id */
      operation_id: string | null;
      /** Created At */
      created_at: string;
    };
    /** SetFixInput */
    SetFixInput: {
      /** Version Id */
      version_id: string;
      /**
       * Action
       * @enum {string}
       */
      action: "reshoot" | "extend" | "retract" | "keep";
      /** Target */
      target: {
        [key: string]: unknown;
      };
      /**
       * Shows
       * @default
       */
      shows: string;
      /** Issue Id */
      issue_id?: string | null;
    };
    /** SetHideRegionDTO */
    SetHideRegionDTO: {
      /** Id */
      id: string;
      /** Version Id */
      version_id: string;
      /**
       * Shape
       * @enum {string}
       */
      shape: "box" | "sphere";
      /** Params */
      params: {
        [key: string]: unknown;
      };
      /** Label */
      label: string;
      /** Created At */
      created_at: string;
    };
    /** SetHideRegionInput */
    SetHideRegionInput: {
      /** Version Id */
      version_id: string;
      /**
       * Shape
       * @enum {string}
       */
      shape: "box" | "sphere";
      /** Params */
      params: {
        [key: string]: unknown;
      };
      /**
       * Label
       * @default
       */
      label: string;
    };
    /** SetImportInput */
    SetImportInput: {
      /** Folder */
      folder: string;
    };
    /** SetIssueDTO */
    SetIssueDTO: {
      /** Id */
      id: string;
      /** Found In Id */
      found_in_id: string;
      /** Resolved In Id */
      resolved_in_id: string | null;
      /**
       * Kind
       * @enum {string}
       */
      kind:
        | "shape"
        | "space"
        | "duplicate"
        | "stairs"
        | "obstacle"
        | "pocket"
        | "floaters"
        | "hole"
        | "split"
        | "doubled"
        | "two-way"
        | "other";
      /**
       * Author
       * @enum {string}
       */
      author: "human" | "agent" | "judge";
      /**
       * Status
       * @enum {string}
       */
      status: "open" | "queued" | "fixed" | "dismissed" | "failed";
      /** Cells */
      cells: string[];
      /** Cameras */
      cameras: string[];
      /** Region */
      region: {
        [key: string]: unknown;
      } | null;
      /** Why */
      why: string;
      /** Note */
      note: string;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
    };
    /** SetIssueInput */
    SetIssueInput: {
      /** Version Id */
      version_id: string;
      /**
       * Kind
       * @enum {string}
       */
      kind:
        | "shape"
        | "space"
        | "duplicate"
        | "stairs"
        | "obstacle"
        | "pocket"
        | "floaters"
        | "hole"
        | "split"
        | "doubled"
        | "two-way"
        | "other";
      /** Why */
      why: string;
      /** Cells */
      cells?: string[];
      /** Cameras */
      cameras?: string[];
      region?: components["schemas"]["SetRegionDTO"] | null;
      /**
       * Note
       * @default
       */
      note: string;
    };
    /** SetIssueStatusInput */
    SetIssueStatusInput: {
      /**
       * Status
       * @enum {string}
       */
      status: "open" | "dismissed" | "fixed";
      /** Expected Revision */
      expected_revision: number;
      /** Version Id */
      version_id?: string | null;
    };
    /** SetKeyframeDTO */
    SetKeyframeDTO: {
      /** Id */
      id: string;
      /** Label */
      label: string;
      /** Key */
      key: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "erp" | "pinhole";
      /**
       * Role
       * @enum {string}
       */
      role: "observed" | "generated" | "trajectory" | "appearance-only";
      /** Viewmat */
      viewmat: number[][];
      /** Intrinsics */
      intrinsics: number[][] | null;
      /** Width */
      width: number;
      /** Height */
      height: number;
      /** Heading */
      heading: number | null;
      image: components["schemas"]["SetMediaDTO"];
      /** Was */
      was?: string | null;
      /** Dropped */
      dropped?: string | null;
      /**
       * Can Keep
       * @default false
       */
      can_keep: boolean;
    };
    /**
     * SetMapDTO
     * @description The eye-level map: `plane` int8 (0 unknown, 1 free, 2 occupied) indexed [x, z], row-major, base64;
     *     `frontier` the same grid bit-packed (numpy packbits order), base64.
     */
    SetMapDTO: {
      /** Version Id */
      version_id: string;
      /** Shape */
      shape: number[];
      /** Plane */
      plane: string;
      /** Frontier */
      frontier: string;
      /** Meta */
      meta: {
        [key: string]: unknown;
      };
    };
    /** SetMediaDTO */
    SetMediaDTO: {
      /** Thumbnail */
      thumbnail: string;
      /** Original */
      original: string;
    };
    /** SetPlaceAngleDTO */
    SetPlaceAngleDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
    };
    /**
     * SetPlaceDTO
     * @description A Direction place built as the set, with the angles its views can be aimed at (ADR 0019 §2).
     */
    SetPlaceDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Angles */
      angles: components["schemas"]["SetPlaceAngleDTO"][];
    };
    /**
     * SetRegionDTO
     * @description Where an issue is: a world point or box, a fly-through time, the camera it was seen from, and, where no
     *     geometry answered (a hole), the spot on that camera's picture (`screen`, 0..1 across and down).
     */
    SetRegionDTO: {
      /** Point */
      point?: number[] | null;
      /** Box */
      box?: {
        [key: string]: unknown;
      } | null;
      /** Time */
      time?: number | null;
      /** Viewmat */
      viewmat?: number[][] | null;
      /** Fov */
      fov?: number | null;
      /** Screen */
      screen?: number[] | null;
    };
    /** SetRemovedInput */
    SetRemovedInput: {
      /** Removed */
      removed: boolean;
      /** Version Id */
      version_id?: string | null;
    };
    /**
     * SetRunDTO
     * @description What Run queued fixes would do on the shown version: the fixes it runs and Python's words for it.
     */
    SetRunDTO: {
      /** Fix Ids */
      fix_ids: string[];
      /** Words */
      words: string;
    };
    /**
     * SetSeedInput
     * @description Build a set from one shot of a segment's current take (ADR 0019 §4), filling in what the take never saw unless
     *     asked not to (ADR 0020).
     */
    SetSeedInput: {
      /** Segment Id */
      segment_id: string;
      /** Shot */
      shot: number;
      /** Name */
      name: string;
      /**
       * Use For Place
       * @default false
       */
      use_for_place: boolean;
      /**
       * Fill Unseen
       * @default true
       */
      fill_unseen: boolean;
    };
    /** SetSplatDTO */
    SetSplatDTO: {
      /** Artifact Id */
      artifact_id: string;
      /** Url */
      url: string;
      /** Size */
      size: number | null;
      /** Gaussians */
      gaussians: number | null;
      /** Sh Degree */
      sh_degree: number | null;
    };
    /** SetStateDTO */
    SetStateDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Place Text */
      place_text: string;
      /** Seed */
      seed: number | null;
      /** Revision */
      revision: number;
      /** Provenance */
      provenance: {
        [key: string]: unknown;
      };
      /** Current Version Id */
      current_version_id: string | null;
      /** Versions */
      versions: components["schemas"]["SetVersionSummaryDTO"][];
      version: components["schemas"]["SetVersionDTO"];
      /** Issues */
      issues: components["schemas"]["SetIssueDTO"][];
      /** Fixes */
      fixes: components["schemas"]["SetFixDTO"][];
      /** Hide Regions */
      hide_regions: components["schemas"]["SetHideRegionDTO"][];
      /** Views */
      views: components["schemas"]["SetViewDTO"][];
      build?: components["schemas"]["SetBuildDTO"] | null;
      run: components["schemas"]["SetRunDTO"];
    };
    /** SetSummaryDTO */
    SetSummaryDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Place Text */
      place_text: string;
      /** Revision */
      revision: number;
      /** Updated At */
      updated_at: string;
      /** Current Version Id */
      current_version_id: string | null;
      shown_version: components["schemas"]["SetVersionSummaryDTO"];
      /** Versions */
      versions: number;
      /** Keyframes */
      keyframes: number;
      /** Open Issues */
      open_issues: number;
      poster: components["schemas"]["SetMediaDTO"] | null;
      /** Has Splat */
      has_splat: boolean;
    };
    /** SetVersionDTO */
    SetVersionDTO: {
      /** Id */
      id: string;
      /** Ordinal */
      ordinal: number;
      /**
       * State
       * @enum {string}
       */
      state: "proposed" | "accepted" | "discarded";
      /**
       * Origin
       * @enum {string}
       */
      origin: "import" | "seed" | "expand" | "repair" | "human";
      /** Parent Id */
      parent_id?: string | null;
      /** Operation Id */
      operation_id?: string | null;
      /** Note */
      note: string;
      /** Created At */
      created_at: string;
      /** Decided At */
      decided_at: string | null;
      /** Map Meta */
      map_meta: {
        [key: string]: unknown;
      };
      /** Metrics */
      metrics: {
        [key: string]: unknown;
      };
      /** Has Map */
      has_map: boolean;
      splat: components["schemas"]["SetSplatDTO"] | null;
      /** Keyframes */
      keyframes: components["schemas"]["SetKeyframeDTO"][];
    };
    /** SetVersionSummaryDTO */
    SetVersionSummaryDTO: {
      /** Id */
      id: string;
      /** Ordinal */
      ordinal: number;
      /**
       * State
       * @enum {string}
       */
      state: "proposed" | "accepted" | "discarded";
      /**
       * Origin
       * @enum {string}
       */
      origin: "import" | "seed" | "expand" | "repair" | "human";
      /** Parent Id */
      parent_id?: string | null;
      /** Operation Id */
      operation_id?: string | null;
      /** Note */
      note: string;
      /** Created At */
      created_at: string;
      /** Decided At */
      decided_at: string | null;
    };
    /**
     * SetViewAngleDTO
     * @description The Direction angle a saved view is the camera of (ADR 0019 §2).
     */
    SetViewAngleDTO: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Place Name */
      place_name: string;
    };
    /** SetViewDTO */
    SetViewDTO: {
      still?: components["schemas"]["SetMediaDTO"] | null;
      angle?: components["schemas"]["SetViewAngleDTO"] | null;
      /** Id */
      id: string;
      /** Version Id */
      version_id: string;
      /** Name */
      name: string;
      /** Viewmat */
      viewmat: number[][];
      /** Fov */
      fov: number;
      /** Width */
      width: number;
      /** Height */
      height: number;
      /** Hide Region Ids */
      hide_region_ids: string[];
      /**
       * Author
       * @enum {string}
       */
      author: "human" | "agent";
      /** Asset Id */
      asset_id: string | null;
      /** Created At */
      created_at: string;
    };
    /** SetViewInput */
    SetViewInput: {
      /** Version Id */
      version_id: string;
      /**
       * Name
       * @default
       */
      name: string;
      /** Viewmat */
      viewmat: number[][];
      /** Fov */
      fov: number;
      /** Width */
      width: number;
      /** Height */
      height: number;
    };
    /**
     * SetViewsInput
     * @description Render a place's angles from the set it is built as (ADR 0019 §3).
     */
    SetViewsInput: {
      /** Request Key */
      request_key?: string | null;
      /** Place Id */
      place_id: string;
      /** Angle Ids */
      angle_ids: string[];
    };
    /** SheetInput */
    SheetInput: {
      /** Request Key */
      request_key?: string | null;
      /** Place Id */
      place_id: string;
      /** Tiles */
      tiles: (string | [number, number])[];
      /** Origin */
      origin: string;
    };
    /** ShotCheckDTO */
    ShotCheckDTO: {
      /** Check */
      check: string;
      /**
       * Verdict
       * @enum {string}
       */
      verdict: "pass" | "fail" | "unsupported";
      /** Calibrated */
      calibrated: boolean;
      /** Measured */
      measured: number | null;
      /** Reason */
      reason: string;
    };
    /** ShotChoiceDTO */
    ShotChoiceDTO: {
      /** Version */
      version: string;
      /** Chosen At */
      chosen_at: string;
      /** Revision */
      revision: number;
    };
    /** ShotDTO */
    ShotDTO: {
      /** Description */
      description: string;
      /** Screen Direction */
      screen_direction: string;
      /** Sound */
      sound: string;
      /** Cast */
      cast?: string[];
    };
    /** ShotLayerDTO */
    ShotLayerDTO: {
      /** Character Id */
      character_id: string;
      /**
       * State
       * @enum {string}
       */
      state: "rendering" | "settled" | "cancelled" | "failed";
      /** Windows Settled */
      windows_settled: number;
      /** Windows Total */
      windows_total: number;
      /** Renders Reserved */
      renders_reserved: number;
    };
    /** ShotPatchInput */
    ShotPatchInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Included */
      included?: boolean | null;
      /** Priority */
      priority?: number | null;
    };
    /** ShotPlanDTO */
    ShotPlanDTO: {
      /** Performers */
      performers: string[];
      /**
       * Grouping
       * @enum {string}
       */
      grouping: "single" | "joint";
      /** Windows */
      windows: components["schemas"]["WindowDTO"][];
      /** Renders Per Window */
      renders_per_window: number;
      /** Gaps */
      gaps: components["schemas"]["RecastGapDTO"][];
      /** Canvas Scale */
      canvas_scale: number | null;
      /** Reasons */
      reasons?: string[];
    };
    /** ShotRevealDTO */
    ShotRevealDTO: {
      /** Shot Id */
      shot_id: string;
      /** Versions */
      versions: {
        [key: string]: unknown;
      }[];
    };
    /**
     * ShotVersionDTO
     * @description One complete replacement of the shot (all windows assembled); anonymous until reveal.
     */
    ShotVersionDTO: {
      /** Id */
      id: string;
      /** Letter */
      letter: string;
      /** Asset Id */
      asset_id: string;
      /** Checks */
      checks: components["schemas"]["ShotCheckDTO"][];
    };
    /** SimilarCharacterDTO */
    SimilarCharacterDTO: {
      /** Character Id */
      character_id: string;
      /** Similarity */
      similarity: number;
    };
    /** SpeechDTO */
    SpeechDTO: {
      /** Speaker Id */
      speaker_id: string;
      /** At Frame */
      at_frame: number;
      /** Text */
      text: string;
      /** Language */
      language: string;
      /** Delivery */
      delivery?: string;
      /** Off Screen */
      off_screen?: boolean;
    };
    /** StateDTO */
    StateDTO: {
      /** At Frame */
      at_frame: number;
      /** Subject Id */
      subject_id: string;
      /** Attribute */
      attribute: string;
      /** Value */
      value: string;
    };
    /**
     * Status
     * @enum {string}
     */
    Status: "default" | "option" | "experimental" | "retired";
    /** SubjectCommandDTO */
    SubjectCommandDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Movie Id */
      movie_id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Keeps */
      keeps: string[];
      /** Identity Asset Id */
      identity_asset_id: string | null;
      receipt: components["schemas"]["ReceiptDTO"];
    };
    /** SubjectCreate */
    SubjectCreate: {
      /** Expected Revision */
      expected_revision: number;
      /** Id */
      id?: string | null;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Keeps */
      keeps: string[];
      /** Identity Asset Id */
      identity_asset_id: string | null;
    };
    /** SubjectDTO */
    SubjectDTO: {
      /** Id */
      id: string;
      /** Revision */
      revision: number;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
      /** Movie Id */
      movie_id: string;
      /** Name */
      name: string;
      /** Description */
      description: string;
      /** Keeps */
      keeps: string[];
      /** Identity Asset Id */
      identity_asset_id: string | null;
    };
    /** SubjectPatch */
    SubjectPatch: {
      /** Expected Revision */
      expected_revision: number;
      /** Name */
      name?: string | null;
      /** Description */
      description?: string | null;
      /** Keeps */
      keeps?: string[] | null;
      /** Identity Asset Id */
      identity_asset_id?: string | null;
    };
    /** SurveyInput */
    SurveyInput: {
      /** Request Key */
      request_key?: string | null;
      /** Place Id */
      place_id: string;
      /** Plan */
      plan: {
        [key: string]: unknown;
      };
      /** Seed */
      seed: number;
    };
    /** TargetDTO */
    TargetDTO: {
      reference?: components["schemas"]["ReferenceDTO"] | null;
      /**
       * Face
       * @enum {string}
       */
      face: "checking" | "face" | "no_face" | "failed";
      /** Asset Id */
      asset_id: string;
      /** Label */
      label: string;
      /**
       * State
       * @enum {string}
       */
      state: "proposed" | "confirmed";
      /**
       * Proposed By
       * @enum {string}
       */
      proposed_by: "user" | "agent";
    };
    /** TargetInput */
    TargetInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Asset Id */
      asset_id: string;
      /** Label */
      label: string;
    };
    /** TelemetrySettingsDTO */
    TelemetrySettingsDTO: {
      /**
       * Enabled
       * @default false
       */
      enabled: boolean;
      /**
       * Base Url
       * @default https://cloud.langfuse.com
       */
      base_url: string;
      /**
       * Environment
       * @default development
       */
      environment: string;
      /**
       * Storage Limit Gib
       * @default 10
       */
      storage_limit_gib: number;
      /**
       * Credential Storage
       * @default session
       */
      credential_storage: string;
      /** Public Key Hint */
      public_key_hint?: string | null;
      /**
       * Has Secret
       * @default false
       */
      has_secret: boolean;
      /** Project Id */
      project_id?: string | null;
    };
    /** TelemetryStatus */
    TelemetryStatus: {
      /** Enabled */
      enabled: boolean;
      /** Connection */
      connection: string;
      /** Observations */
      observations: {
        [key: string]: number;
      };
      /** Media */
      media: {
        [key: string]: number;
      };
      /** Bytes */
      bytes: number;
      /** Capture Error */
      capture_error: string | null;
      /** Last Delivery */
      last_delivery: number | null;
      /** Errors */
      errors: {
        [key: string]: unknown;
      }[];
    };
    /** TraceLink */
    TraceLink: {
      /** Url */
      url: string;
    };
    /** TurnDTO */
    TurnDTO: {
      /** Id */
      id: string;
      /** Ordinal */
      ordinal: number;
      /** Role */
      role: string;
      /** State */
      state: string;
      /** Action */
      action: {
        [key: string]: unknown;
      } | null;
      /** Result */
      result: {
        [key: string]: unknown;
      } | null;
      /** Receipts */
      receipts: {
        [key: string]: unknown;
      }[];
      /** Provenance */
      provenance: {
        [key: string]: unknown;
      } | null;
      /** Created At */
      created_at: string;
      /** Updated At */
      updated_at: string;
    };
    /** UndoDTO */
    UndoDTO: {
      /** Id */
      id: string;
      /** Movie Id */
      movie_id: string;
      /** Command */
      command: string;
      /**
       * Target Kind
       * @enum {string}
       */
      target_kind:
        "movie" | "subject" | "segment" | "phase" | "cut" | "angle" | "place";
      /** Target Id */
      target_id: string;
      /** Before */
      before: {
        [key: string]: unknown;
      };
      /** After */
      after: {
        [key: string]: unknown;
      };
      /** Undoes */
      undoes: string | null;
      /** Undone At */
      undone_at: string | null;
      /** Created At */
      created_at: string;
      /** Record */
      record: {
        [key: string]: unknown;
      };
    };
    /** UnlockCredentials */
    UnlockCredentials: {
      /**
       * Passphrase
       * Format: password
       */
      passphrase: string;
    };
    /**
     * UseSetInput
     * @description Use a set for a scene (ADR 0019 §5): a segment without an opening angle names one, or a place and a new
     *     angle's name.
     */
    UseSetInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Set Id */
      set_id: string;
      /** Angle Id */
      angle_id?: string | null;
      /** Place Id */
      place_id?: string | null;
      /** Angle Name */
      angle_name?: string | null;
    };
    /**
     * UseSetPreviewDTO
     * @description What Use set would change, said before it does.
     */
    UseSetPreviewDTO: {
      /** Set Name */
      set_name: string;
      /** Accepted */
      accepted: boolean;
      /** Place Name */
      place_name: string;
      /** Angle Name */
      angle_name: string;
      /** New Angle */
      new_angle: boolean;
      /** Relinks From */
      relinks_from: string | null;
      /** Other Scenes */
      other_scenes: components["schemas"]["UseSetSceneDTO"][];
      /** First Cut Changes */
      first_cut_changes: boolean;
      /** Camera */
      camera: string | null;
    };
    /** UseSetSceneDTO */
    UseSetSceneDTO: {
      /** Segment Id */
      segment_id: string;
      /** Movie */
      movie: string;
      /** Label */
      label: string;
    };
    /** ValidationError */
    ValidationError: {
      /** Location */
      loc: (string | number)[];
      /** Message */
      msg: string;
      /** Error Type */
      type: string;
      /** Input */
      input?: unknown;
      /** Context */
      ctx?: Record<string, never>;
    };
    /**
     * VoiceDTO
     * @description A character's voice (ADR 0016 §1, §4): the chosen recordings, the shared build's progress and the audition.
     *     No operation id, artifact id, path, folder or worker text; the audition reel's asset id is the only other id.
     */
    VoiceDTO: {
      /** Samples */
      samples: components["schemas"]["VoiceSampleDTO"][];
      /** Seconds */
      seconds: number;
      /**
       * State
       * @enum {string}
       */
      state: "proposed" | "confirmed";
      /**
       * Proposed By
       * @enum {string}
       */
      proposed_by: "agent" | "user";
      /** Model State */
      model_state:
        ("making" | "waiting" | "approved" | "rejected" | "failed") | null;
      /** Stage */
      stage:
        | (
            | "samples"
            | "seed"
            | "material"
            | "convert"
            | "filter"
            | "train"
            | "package"
            | "audition"
          )
        | null;
      /** Done */
      done: number;
      /** Total */
      total: number;
      /** Error */
      error: string | null;
      /** Reason */
      reason: string | null;
      /** Audition Asset Id */
      audition_asset_id: string | null;
      /** Library Changed */
      library_changed: boolean;
      /** Blocked */
      blocked: boolean;
    };
    /**
     * VoiceLibraryDTO
     * @description The expressive recordings folder (ADR 0016 P5): its state and what a voice build would use from it.
     */
    VoiceLibraryDTO: {
      /** Folder */
      folder: string;
      /**
       * State
       * @enum {string}
       */
      state: "not_set" | "missing" | "ready";
      /** Clips */
      clips: number;
      /** Categories */
      categories: {
        [key: string]: number;
      };
      /** Skipped */
      skipped: number;
    };
    /**
     * VoiceSampleDTO
     * @description One Library audio recording of the target voice, in the order the user chose it.
     */
    VoiceSampleDTO: {
      /** Asset Id */
      asset_id: string;
      /** Name */
      name: string;
      /** Seconds */
      seconds: number;
    };
    /**
     * VoiceSamplesInput
     * @description Library audio recordings of the target voice, in order: 15 to 60 seconds in all.
     */
    VoiceSamplesInput: {
      /** Expected Revision */
      expected_revision: number;
      /** Asset Ids */
      asset_ids: string[];
    };
    /** Whiteboard */
    Whiteboard: {
      /**
       * Version
       * @default 1
       * @constant
       */
      version: 1;
      /**
       * Revision
       * @default 0
       */
      revision: number;
      /**
       * Brief
       * @default
       */
      brief: string;
      /**
       * Understanding
       * @default
       */
      understanding: string;
      /** Decisions */
      decisions?: components["schemas"]["BoardEntry"][];
      /** References */
      references?: components["schemas"]["BoardEntry"][];
      /** Open Questions */
      open_questions?: components["schemas"]["BoardEntry"][];
    };
    /** WindowDTO */
    WindowDTO: {
      /** Index */
      index: number;
      /** Start */
      start: number;
      /** End */
      end: number;
      /** Length */
      length: number;
      /** Operation Id */
      operation_id: string | null;
      /** State */
      state: string | null;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
  session_api_v1_session_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionDTO"];
        };
      };
    };
  };
  health_api_v1_health_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HealthDTO"];
        };
      };
    };
  };
  get_library_api_v1_library_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LibraryState"];
        };
      };
    };
  };
  create_library_api_v1_library_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateLibrary"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LibraryDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  import_media_api_v1_imports_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "multipart/form-data": components["schemas"]["Body_import_media_api_v1_imports_post"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ImportDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  assets_api_v1_assets_get: {
    parameters: {
      query?: {
        q?: string;
        kind?: string;
        collection?: string;
        offset?: number;
        limit?: number;
        imported_only?: boolean;
        exclude?: string;
        keep?: string;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  media_urls_api_v1_assets__asset_id__urls_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  asset_api_v1_assets__asset_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  review_asset_api_v1_assets__asset_id__review_patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReviewInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  collections_api_v1_collections_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
    };
  };
  create_collection_api_v1_collections_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["NamedInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  assign_collection_api_v1_collections__collection_id__assets_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        collection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AssociationInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  entities_api_v1_entities_get: {
    parameters: {
      query?: {
        kind?: string;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_entity_api_v1_entities_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EntityInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_entity_api_v1_entities__entity_id__put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        entity_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EntityInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  assign_entity_api_v1_entities__entity_id__assets_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        entity_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AssociationInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  media_file_api_v1_media__asset_id___rendition__get: {
    parameters: {
      query: {
        expires: number;
        signature: string;
      };
      header?: never;
      path: {
        asset_id: string;
        rendition: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  credentials_api_v1_settings_assistant_credentials_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CredentialState"];
        };
      };
    };
  };
  save_api_v1_settings_assistant_credentials_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SaveCredential"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CredentialState"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  unlock_api_v1_settings_assistant_credentials_unlock_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UnlockCredentials"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CredentialState"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  lock_api_v1_settings_assistant_credentials_lock_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CredentialState"];
        };
      };
    };
  };
  test_api_v1_settings_assistant_connections__connection_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: {
      content: {
        "application/json": components["schemas"]["ConnectionProbe"] | null;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionTest"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  settings_api_v1_settings_telemetry_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TelemetrySettingsDTO"];
        };
      };
    };
  };
  save_api_v1_settings_telemetry_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SaveTelemetry"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TelemetrySettingsDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  status_api_v1_settings_telemetry_status_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TelemetryStatus"];
        };
      };
    };
  };
  test_api_v1_settings_telemetry_test_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionResult"];
        };
      };
    };
  };
  retry_api_v1_settings_telemetry_retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TelemetryStatus"];
        };
      };
    };
  };
  link_api_v1_settings_telemetry_links__trace_id__get: {
    parameters: {
      query?: {
        kind?: "trace" | "session";
      };
      header?: never;
      path: {
        trace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TraceLink"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  settings_api_v1_settings_connections_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionSettings"];
        };
      };
    };
  };
  save_settings_api_v1_settings_connections_put: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectionSettings"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionSettings"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  engine_readiness_api_v1_settings_engines_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnginesDTO"];
        };
      };
    };
  };
  expressive_library_api_v1_settings_voice_library_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["VoiceLibraryDTO"];
        };
      };
    };
  };
  comfy_status_api_v1_settings_comfy_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ComfyStatusDTO"];
        };
      };
    };
  };
  movies_api_direction_movies_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionMovieDTO"][];
        };
      };
    };
  };
  create_movie_api_direction_movies_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MovieInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MovieCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  movie_api_direction_movies__movie_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MovieDetailDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_movie_api_direction_movies__movie_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MoviePatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MovieCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  subjects_api_direction_movies__movie_id__subjects_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SubjectDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  add_subject_api_direction_movies__movie_id__subjects_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SubjectCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SubjectCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_subject_api_direction_subjects__subject_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        subject_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SubjectPatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SubjectCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  remove_subject_api_direction_subjects__subject_id__remove_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        subject_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionRevisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RemovedDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  add_segment_api_direction_movies__movie_id__segments_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SegmentCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SegmentCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  segment_api_direction_segments__segment_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SegmentDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_segment_api_direction_segments__segment_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SegmentPatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SegmentCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  assembled_segment_api_direction_segments__segment_id__assembled_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssembledSegmentDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  use_set_preview_api_direction_segments__segment_id__use_set_get: {
    parameters: {
      query: {
        set_id: string;
        angle_id?: string | null;
        place_id?: string | null;
        angle_name?: string | null;
      };
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["UseSetPreviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  use_set_api_direction_segments__segment_id__use_set_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UseSetInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SegmentDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  build_set_preview_api_direction_segments__segment_id__build_set_get: {
    parameters: {
      query?: {
        shot?: number;
        fill_unseen?: boolean;
      };
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BuildSetPreviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  segment_split_points_api_direction_segments__segment_id__split_points_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionSplitPointDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  split_segment_api_direction_segments__segment_id__split_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionSegmentSplitInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionSegmentSplitCommandDTO"];
        };
      };
      /** @description Unprocessable Entity */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionSplitRefusalDTO"];
        };
      };
    };
  };
  remove_segment_api_direction_segments__segment_id__remove_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionRevisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RemovedDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  move_segment_api_direction_segments__segment_id__move_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SegmentMove"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SegmentCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  add_phase_api_direction_segments__segment_id__phases_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PhaseCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PhaseCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_phase_api_direction_phases__phase_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        phase_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PhasePatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PhaseCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  remove_phase_api_direction_phases__phase_id__remove_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        phase_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionRevisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RemovedDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  receipts_api_direction_movies__movie_id__receipts_get: {
    parameters: {
      query?: {
        limit?: number;
      };
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReceiptDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  undo_api_direction_receipts__receipt_id__undo_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        receipt_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["UndoDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_places_api_direction_places_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PlaceDTO"][];
        };
      };
    };
  };
  create_place_api_direction_places_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["NameInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PlaceDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_place_api_direction_places__place_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        place_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PlaceDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  edit_place_text_api_direction_places__place_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        place_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionPlaceTextPatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionPlaceTextCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  link_place_set_api_direction_places__place_id__set_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        place_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PlaceSetInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PlaceDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_angles_api_direction_places__place_id__angles_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        place_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AngleDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  add_angle_api_direction_places__place_id__angles_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        place_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AngleInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AngleDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  edit_angle_text_api_direction_angles__angle_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        angle_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionAngleTextPatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionAngleTextCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  angle_evidence_api_direction_angles__angle_id__evidence_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        angle_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EvidenceDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  approve_evidence_api_direction_evidence__evidence_id__approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        evidence_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EvidenceDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_cut_api_direction_cuts_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CutCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CutCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_cut_api_direction_cuts__cut_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CutDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_cut_api_direction_cuts__cut_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CutPatch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CutCommandDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  cut_keyframes_api_direction_cuts__cut_id__keyframes_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["KeyframeDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  remove_cut_api_direction_cuts__cut_id__remove_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DirectionRevisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RemovedDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  cut_suitability_api_direction_cuts__cut_id__suitability_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CutSuitabilityDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  judge_cut_suitability_api_direction_cuts__cut_id__suitability_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CutSuitabilityInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CutSuitabilityDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_cut_plan_api_direction_cuts__cut_id__plan_get: {
    parameters: {
      query?: {
        integration?: string;
      };
      header?: never;
      path: {
        cut_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CutPlanDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_segment_plan_api_direction_segments__segment_id__plan_get: {
    parameters: {
      query?: {
        integration?: string;
        sheet_asset?: string | null;
        context_artifact?: string | null;
      };
      header?: never;
      path: {
        segment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DirectionSegmentPlanDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  people_analysis_list_api_production_people_analyses_get: {
    parameters: {
      query: {
        asset_id: string;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReplacementPeopleAnalysisDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  people_analysis_api_production_people_analyses_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplacementPeopleAnalysisInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replacement_preview_api_production_replacement_plans_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplacementPlanInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReplacementPlanDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replacement_start_api_production_replacements_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplacementStartInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replacement_review_api_production_replacements__operation_id__review_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  recorded_plan_api_production_replacements__operation_id__plan_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReplacementPlanDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replacement_approval_api_production_replacements_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CandidateInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReplacementApprovalDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  empty_plate_api_production_plates_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EmptyPlateInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  removal_api_production_removals_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RemovalInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  plate_review_api_production_removals__operation_id__review_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  plate_approval_api_production_plates_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CandidateInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PlaceDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  survey_api_production_surveys_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SurveyInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  survey_review_api_production_surveys__operation_id__review_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_view_renders_api_production_set_views_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetViewsInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_seed_start_api_production_set_seeds_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetSeedInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_seed_retry_api_production_set_seeds__operation_id__retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_views_review_api_production_set_views__operation_id__review_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  view_approval_api_production_views_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CandidateInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProductionEvidenceDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  insertion_api_production_insertions_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["InsertionInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  insertion_review_api_production_insertions__operation_id__review_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  pin_approval_api_production_pins_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PinApprovalInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProductionKeyframeDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  sheet_tiles_api_production_places__place_id__sheet_tiles_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        place_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProductionSheetTilesDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  sheet_api_production_sheets_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SheetInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  render_api_production_renders_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RenderInput"];
      };
    };
    responses: {
      /** @description Existing operation */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  render_review_api_production_renders__operation_id__review_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  recent_operations_api_production_operations_get: {
    parameters: {
      query?: {
        kind?: string | null;
        state?: string | null;
        limit?: number;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  operation_api_production_operations__operation_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  operation_summary_api_production_operations__operation_id__summary_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationSummaryDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  cancel_operation_api_production_operations__operation_id__cancel_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        operation_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  taxonomy_api_production_review_tags_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewTaxonomyDTO"];
        };
      };
    };
  };
  measure_api_production_candidates__candidate_id__prescreen_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        candidate_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PrescreenDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  blind_review_api_production_reviews__review_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        review_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reveal_review_api_production_reviews__review_id__reveal_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        review_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reject_references_api_production_reviews__review_id__reject_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        review_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProductionReferenceRejectionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ReviewDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  ranking_api_production_reviews__review_id__ranking_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        review_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RankingInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RankingDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  select_api_production_reviews__review_id__select_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        review_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SelectInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SelectionDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  read_selection_state_api_production_selections_get: {
    parameters: {
      query: {
        movie_id: string;
        segment_id: string;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SelectionsDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  readiness_api_export_movies__movie_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MovieExportDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  export_api_export_movies__movie_id__post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        movie_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MovieExportInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  recasts_api_recasts_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastSummaryDTO"][];
        };
      };
    };
  };
  create_api_recasts_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateRecastInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_api_recasts__recast_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  estimate_api_recasts__recast_id__estimate_get: {
    parameters: {
      query?: {
        mode?: "draft" | "final";
        start?: number | null;
        end?: number | null;
      };
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EstimateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  patch_character_api_recasts__recast_id__characters__character_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CharacterPatchInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  merge_api_recasts__recast_id__characters_merge_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MergeInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  not_same_api_recasts__recast_id__not_same_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["NotSameInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  move_tracks_api_recasts__recast_id__tracks_move_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MoveTracksInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  undo_edit_api_recasts__recast_id__edits__edit_id__undo_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        edit_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  propose_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__propose_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
        analysis_operation_id: string;
        track_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AppearanceProposalInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  confirm_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
        analysis_operation_id: string;
        track_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AppearanceDecisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reject_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__reject_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
        analysis_operation_id: string;
        track_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AppearanceDecisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  clear_appearance_api_recasts__recast_id__shots__shot_id__appearances__analysis_operation_id___track_id__delete: {
    parameters: {
      query: {
        expected_revision: number;
      };
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
        analysis_operation_id: string;
        track_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  suggest_appearances_api_recasts__recast_id__shots__shot_id__appearances_suggest_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_target_api_recasts__recast_id__characters__character_id__target_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TargetInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  clear_target_api_recasts__recast_id__characters__character_id__target_delete: {
    parameters: {
      query: {
        expected_revision: number;
      };
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  confirm_target_api_recasts__recast_id__characters__character_id__target_confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  approve_reference_api_recasts__recast_id__characters__character_id__reference_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reject_reference_api_recasts__recast_id__characters__character_id__reference_reject_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_voice_api_recasts__recast_id__characters__character_id__voice_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["VoiceSamplesInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  clear_voice_api_recasts__recast_id__characters__character_id__voice_delete: {
    parameters: {
      query: {
        expected_revision: number;
      };
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  confirm_voice_api_recasts__recast_id__characters__character_id__voice_confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  build_voice_api_recasts__recast_id__characters__character_id__voice_build_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["BuildVoiceInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retry_voice_api_recasts__recast_id__characters__character_id__voice_retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  approve_voice_api_recasts__recast_id__characters__character_id__voice_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reject_voice_api_recasts__recast_id__characters__character_id__voice_reject_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        character_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  patch_shot_api_recasts__recast_id__shots__shot_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ShotPatchInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  plan_api_recasts__recast_id__plan_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PlanInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  run_api_recasts__recast_id__run_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RunInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retry_analysis_api_recasts__recast_id__analysis_retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  pause_api_recasts__recast_id__run_pause_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  cancel_api_recasts__recast_id__run_cancel_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  choose_api_recasts__recast_id__shots__shot_id__choice_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ChoiceInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  head_pass_api_recasts__recast_id__shots__shot_id__head_pass_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["HeadPassInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retake_api_recasts__recast_id__shots__shot_id__retake_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RetakeInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reveal_api_recasts__recast_id__shots__shot_id__reveal_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        shot_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ShotRevealDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  start_dialogue_api_recasts__recast_id__dialogue_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retry_dialogue_api_recasts__recast_id__dialogue_retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionInputDTO"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  lines_api_recasts__recast_id__lines_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LinesDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  confirm_lines_api_recasts__recast_id__lines_confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["BulkConfirmInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  confirm_line_api_recasts__recast_id__lines__segment_key__confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        segment_key: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LineDecisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reject_line_api_recasts__recast_id__lines__segment_key__reject_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        segment_key: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LineDecisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  clear_line_api_recasts__recast_id__lines__segment_key__delete: {
    parameters: {
      query: {
        expected_revision: number;
      };
      header?: never;
      path: {
        recast_id: string;
        segment_key: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  edge_line_api_recasts__recast_id__lines__segment_key__edges_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
        segment_key: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LineEdgesInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RecastDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  conform_api_recasts__recast_id__conform_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        recast_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConformInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OperationDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  face_api_recasts__recast_id__faces__artifact_id__get: {
    parameters: {
      query: {
        expires: number;
        signature: string;
      };
      header?: never;
      path: {
        recast_id: string;
        artifact_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_sets_api_sets_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetSummaryDTO"][];
        };
      };
    };
  };
  import_set_api_sets_import_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetImportInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_set_api_sets__set_id__get: {
    parameters: {
      query?: {
        version_id?: string | null;
      };
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_map_api_sets__set_id__versions__version_id__map_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        version_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetMapDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  mark_issue_api_sets__set_id__issues_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetIssueInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  issue_status_api_sets__set_id__issues__issue_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        issue_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetIssueStatusInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  queue_fix_api_sets__set_id__fixes_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetFixInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  fill_more_api_sets__set_id__fill_more_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetBuildInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  cancel_fix_api_sets__set_id__fixes__fix_id__delete: {
    parameters: {
      query?: {
        version_id?: string | null;
      };
      header?: never;
      path: {
        set_id: string;
        fix_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retry_fix_api_sets__set_id__fixes__fix_id__retry_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        fix_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetBuildInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  run_fixes_api_sets__set_id__build_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetBuildInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  hide_region_api_sets__set_id__hide_regions_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetHideRegionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  hide_region_removed_api_sets__set_id__hide_regions__region_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        region_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetRemovedInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  save_view_api_sets__set_id__views_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetViewInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_view_still: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        view_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "multipart/form-data": components["schemas"]["Body_set_view_still"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  places_built_as_api_sets__set_id__places_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetPlaceDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  aim_view_api_sets__set_id__views__view_id__angle_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        view_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetAimInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  view_removed_api_sets__set_id__views__view_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        view_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetRemovedInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  accept_api_sets__set_id__versions__version_id__accept_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        version_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetAcceptInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  discard_api_sets__set_id__versions__version_id__discard_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        set_id: string;
        version_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SetStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  splat_api_sets__set_id__splats__artifact_id__get: {
    parameters: {
      query: {
        expires: number;
        signature: string;
      };
      header?: never;
      path: {
        set_id: string;
        artifact_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  model_status_api_agent_model_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentModelStatusDTO"];
        };
      };
    };
  };
  sessions_api_agent_sessions_get: {
    parameters: {
      query?: {
        movie_id?: string | null;
        recast_id?: string | null;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"][];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  open_session_api_agent_sessions_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SessionInput"];
      };
    };
    responses: {
      /** @description Existing session */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  new_conversation_api_agent_sessions__session_id__new_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentConversationRevisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  stop_plan_api_agent_sessions__session_id__plan_stop_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentConversationRevisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_session_api_agent_sessions__session_id__get: {
    parameters: {
      query?: {
        after?: number;
      };
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  message_api_agent_sessions__session_id__messages_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MessageInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  board_api_agent_sessions__session_id__board_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["BoardInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  cancel_api_agent_sessions__session_id__cancel_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  approve_plan_api_agent_sessions__session_id__plan_approve_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentPlanDecisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  revise_plan_api_agent_sessions__session_id__plan_revise_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentPlanReviseInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reject_plan_api_agent_sessions__session_id__plan_reject_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentPlanDecisionInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionStateDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  plan_mode_api_agent_sessions__session_id__plan_mode_put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentPlanModeInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HarnessSessionDTO"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
}
