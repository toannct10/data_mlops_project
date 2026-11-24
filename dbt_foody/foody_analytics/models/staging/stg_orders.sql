with source as (

    select *
    from {{ source('foody_raw', 'orders') }}

)

select
    order_id,
    canonical_order_id,
    source_order_id,
    restaurant_id,
    restaurant_name,
    try_to_date(date_key::varchar, 'YYYYMMDD') as order_date,
    sales_amount,
    total_amount,
    order_status,
    source,
    source_dataset,
    created_at,
    updated_at,
    delivered_at,
    cancelled_at,
    source_file,
    loaded_at

from source