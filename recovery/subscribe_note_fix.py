from __future__ import annotations

def __update_subscribe_note(subscribe: Subscribe, downloads: Optional[List[Context]]):
    """
        更新已下载信息到note字段
        """
    if not downloads:
        return
    note = []
    if subscribe.note:
        note = subscribe.note or []
    for context in downloads:
        meta = context.meta_info
        mediainfo = context.media_info
        if subscribe.tmdbid and mediainfo.tmdb_id and (mediainfo.tmdb_id != subscribe.tmdbid):
            continue
        if subscribe.doubanid and mediainfo.douban_id and (mediainfo.douban_id != subscribe.doubanid):
            continue
        subscribe_bangumiid = subscribe.bangumiid
        media_bangumiid = mediainfo.bangumi_id
        if subscribe_bangumiid and media_bangumiid and (media_bangumiid != subscribe_bangumiid):
            continue
        subscribe_anilistid = subscribe.anilistid
        media_anilistid = mediainfo.anilist_id
        if subscribe_anilistid and media_anilistid and (media_anilistid != subscribe_anilistid):
            continue
        subscribe_source, subscribe_media_id = resolve_media_identity(media=subscribe)
        media_source, media_id = resolve_media_identity(media=mediainfo)
        if subscribe_source == media_source and subscribe_media_id and media_id and (subscribe_media_id != media_id):
            continue
        items = []
        if mediainfo.type == MediaType.TV:
            selected = getattr(context, 'selected_episodes', None)
            items = selected if selected is not None else meta.episode_list
        elif mediainfo.type == MediaType.MOVIE:
            items = [1]
        if not items:
            continue
        note = list(set(note).union(set(items)))
    if note:
        SubscribeOper().update(subscribe.id, {'note': note})
