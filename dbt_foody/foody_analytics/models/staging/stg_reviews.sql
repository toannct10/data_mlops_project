with source as (

    select *
    from {{ source('foody_raw', 'reviews') }}

)

select
    review_id,
    canonical_order_id,
    source_order_id,
    order_id,
    restaurant_id,
    restaurant_name,
    try_to_date(date_key::varchar, 'YYYYMMDD') as review_date,
    rating,
    comment,
    review_text,
    predicted_sentiment,
    upper(coalesce(overall_sentiment, predicted_sentiment)) as overall_sentiment,
    absa_model_version,
    review_timestamp,
    created_at,
    coalesce(absa_model_version is not null
      and coalesce(relevance_label, '') <> 'NOT_RELEVANT'
      and upper(coalesce(overall_sentiment, predicted_sentiment)) in ('POSITIVE','NEUTRAL','NEGATIVE'), false) as is_valid_sentiment,
    predicted_aspects,
    absa_predictions,
    relevance_label,
    relevance_score,
    aspect_status,
    source,
    source_dataset,
    source_file,
    loaded_at

from source