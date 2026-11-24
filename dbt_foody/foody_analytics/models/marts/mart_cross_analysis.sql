with sales_monthly as (

    select
        date_trunc('month', order_date) as analysis_month,
        restaurant_id,
        restaurant_name,

        count(*) as total_orders,
        sum(sales_amount) as total_sales,
        avg(sales_amount) as avg_order_value,
        sum(case when upper(order_status)='DELIVERED' then coalesce(total_amount,sales_amount) else 0 end) as delivered_revenue,
        count_if(upper(order_status)='DELIVERED') as delivered_orders

    from {{ ref('stg_orders') }}

    group by
        date_trunc('month', order_date),
        restaurant_id,
        restaurant_name

),

reviews_monthly as (

    select
        date_trunc('month', review_date) as analysis_month,
        restaurant_id,
        restaurant_name,

        count(*) as total_reviews,
        avg(rating) as avg_rating,
        avg(case when source='live_web' then rating*2 else rating end) as avg_rating_10,
        count_if(is_valid_sentiment) as eligible_reviews,

        count_if(is_valid_sentiment and overall_sentiment = 'POSITIVE') as positive_reviews,
        count_if(is_valid_sentiment and overall_sentiment = 'NEUTRAL') as neutral_reviews,
        count_if(is_valid_sentiment and overall_sentiment = 'NEGATIVE') as negative_reviews,

        count_if(relevance_label = 'RELEVANT') as relevant_reviews

    from {{ ref('stg_reviews') }}

    group by
        date_trunc('month', review_date),
        restaurant_id,
        restaurant_name

)

select
    coalesce(s.analysis_month, r.analysis_month) as analysis_month,

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
    r.negative_reviews,
    r.relevant_reviews,

    case
        when r.eligible_reviews > 0
        then r.positive_reviews / r.eligible_reviews::float
    end as positive_rate,

    case
        when r.eligible_reviews > 0
        then r.negative_reviews / r.eligible_reviews::float
    end as negative_rate

from sales_monthly s

full outer join reviews_monthly r
    on s.analysis_month = r.analysis_month
    and s.restaurant_id = r.restaurant_id