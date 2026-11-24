with sales as (

    select
        restaurant_id,
        restaurant_name,
        count(*) as total_orders,
        sum(sales_amount) as total_sales,
        avg(sales_amount) as avg_order_value,
        sum(case when upper(order_status)='DELIVERED' then coalesce(total_amount,sales_amount) else 0 end) as delivered_revenue,
        count_if(upper(order_status)='DELIVERED') as delivered_orders
    from {{ ref('stg_orders') }}
    group by
        restaurant_id,
        restaurant_name

),

reviews as (

    select
        restaurant_id,
        restaurant_name,
        count(*) as total_reviews,
        avg(rating) as avg_rating,
        avg(case when source='live_web' then rating*2 else rating end) as avg_rating_10,
        count_if(is_valid_sentiment) as eligible_reviews,
        count_if(is_valid_sentiment and overall_sentiment = 'POSITIVE') as positive_reviews,
        count_if(is_valid_sentiment and overall_sentiment = 'NEUTRAL') as neutral_reviews,
        count_if(is_valid_sentiment and overall_sentiment = 'NEGATIVE') as negative_reviews
    from {{ ref('stg_reviews') }}
    group by
        restaurant_id,
        restaurant_name

)

select
    coalesce(s.restaurant_id, r.restaurant_id) as restaurant_id,
    coalesce(s.restaurant_name, r.restaurant_name) as restaurant_name,

    s.total_orders,
    s.total_sales,
    s.avg_order_value,
    s.delivered_revenue,
    s.delivered_orders,

    r.total_reviews,
    r.avg_rating,
    r.avg_rating_10,
    r.eligible_reviews,
    r.positive_reviews,
    r.neutral_reviews,
    r.negative_reviews

from sales s

full outer join reviews r
    on s.restaurant_id = r.restaurant_id