select
    date_trunc('month', order_date) as sales_month,
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