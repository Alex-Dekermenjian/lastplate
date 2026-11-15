
"""
URL configuration for the web frontend.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.urls import path
from django.views.generic import RedirectView

from frontend import (
    account_views,
    branch_views,
    browse_views,
    cart,
    console_views,
    customer_console_views,
    health,
    listing_photos,
    listing_views,
    offer_actions,
    offer_reserve,
    order_console_views,
    order_views,
    pickup_views,
    shop_order_views,
    views,
)

urlpatterns = [
    path("", views.home, name="home"),

    # LP-13: Public food browsing (no sign-in).
    path("offers/", browse_views.browse_offers, name="browse-offers"),
    path(
        "offers/<int:listing_id>/",
        browse_views.offer_detail,
        name="offer-detail",
    ),
    # LP-83: a customer reserves one offer from its page.
    path("offers/<int:listing_id>/reserve/", offer_reserve.reserve_offer, name="offer-reserve"),
    # LP-39: a restaurant's page with everything it has left.
    path("restaurants/<int:shop_id>/", browse_views.restaurant_detail, name="restaurant-detail"),

    # LP-62: the customer's cart (one restaurant, kept in the session).
    path("cart/", cart.view_cart, name="cart"),
    path("cart/add/<int:listing_id>/", cart.add_to_cart, name="cart-add"),
    path("cart/update/<int:listing_id>/", cart.update_cart, name="cart-update"),
    # LP-19: Reserve, placing the order from the cart.
    path("cart/checkout/", cart.checkout, name="cart-checkout"),

    # LP-78: the customer's order page (shown at the counter) and My orders.
    path("orders/", order_views.my_orders, name="my-orders"),
    path("orders/<uuid:reservation_ref>/", order_views.order_detail, name="order-detail"),

    # LP-56: one login page and one sign-up page for every kind of account.
    path("login/", account_views.login, name="login"),
    path("signup/", account_views.signup, name="signup"),
    path("sign-out/", account_views.sign_out, name="sign-out"),
    path("account/", account_views.my_account, name="my-account"),
    # Old addresses from LP-7; ?next= and other query strings are kept.
    path("sign-in/", RedirectView.as_view(pattern_name="login", query_string=True), name="sign-in"),
    path("register/", RedirectView.as_view(pattern_name="signup", query_string=True), name="register"),

    # LP-57: the administrator's console (Admin role only).
    path("console/", console_views.console_home, name="console"),
    path("console/restaurants/", console_views.console_restaurants, name="console-restaurants"),
    path(
        "console/restaurants/<int:shop_id>/",
        console_views.console_restaurant,
        name="console-restaurant",
    ),
    # LP-80: customers with expired-order strikes; the admin can remove one.
    path("console/customers/", customer_console_views.console_customers, name="console-customers"),
    path("console/customers/<int:customer_user_id>/", customer_console_views.console_customer,
         name="console-customer"),
    # LP-60: suspend/restore a restaurant, remove an offer, block/unblock a customer.
    path("console/restaurants/<int:shop_id>/suspend/", customer_console_views.suspend_restaurant,
         name="console-suspend-restaurant"),
    path("console/restaurants/<int:shop_id>/restore/", customer_console_views.restore_restaurant,
         name="console-restore-restaurant"),
    path("console/restaurants/<int:shop_id>/offers/<int:listing_id>/remove/", customer_console_views.remove_offer,
         name="console-remove-offer"),
    path("console/customers/<int:customer_user_id>/block/", customer_console_views.block_customer,
         name="console-block-customer"),
    path("console/customers/<int:customer_user_id>/unblock/", customer_console_views.unblock_customer,
         name="console-unblock-customer"),

    # LP-43: every order, the revenue, and orders that need attention.
    path("console/orders/", order_console_views.console_orders, name="console-orders"),
    path("console/revenue/", order_console_views.console_revenue, name="console-revenue"),
    path("console/attention/", order_console_views.console_attention, name="console-attention"),
    path("console/attention/<uuid:reservation_ref>/retry/", order_console_views.retry_order,
         name="console-retry-order"),

    path("workspace/", account_views.workspace, name="workspace"),
    path("workspace/orders/", shop_order_views.today_orders, name="today-orders"),
    path("workspace/orders/count/", shop_order_views.orders_badge, name="orders-badge"),
    path("workspace/orders/pickup/", pickup_views.pickup, name="pickup-code"),
    path("workspace/orders/<uuid:reservation_ref>/paid/", pickup_views.payment, name="pickup-paid"),
    path("workspace/orders/<uuid:reservation_ref>/undo/", pickup_views.payment, {"undo": True}, name="pickup-undo"),
    path("workspace/finish-setup/", account_views.finish_setup, name="finish-setup"),
    path("workspace/profile/", account_views.shop_profile, name="shop-profile"),
    # LP-68: the restaurant's branches (up to 3).
    path("workspace/branches/", branch_views.branches, name="branches"),
    path("workspace/branches/<int:branch_id>/edit/", branch_views.edit_branch, name="branch-edit"),
    path("workspace/branches/<int:branch_id>/remove/", branch_views.remove_branch, name="branch-remove"),

    # LP-10 and LP-11: Listing management.
    path(
        "workspace/listings/new/",
        listing_views.create_listing,
        name="create-listing",
    ),
    path(
        "workspace/listings/<int:listing_id>/edit/",
        listing_views.edit_listing,
        name="edit-listing",
    ),

    # LP-9 and LP-10: Publishing.
    path(
        "workspace/listings/<int:listing_id>/publish/",
        listing_views.publish_listing,
        name="publish-listing",
    ),

    # LP-58: Mark sold out and Delete draft, from the offer list.
    path(
        "workspace/listings/<int:listing_id>/sold-out/",
        offer_actions.mark_sold_out,
        name="mark-sold-out",
    ),
    path(
        "workspace/listings/<int:listing_id>/delete/",
        offer_actions.delete_draft,
        name="delete-draft",
    ),
    # LP-58: /business is the restaurant's area; it is the workspace.
    path("business/", RedirectView.as_view(pattern_name="workspace", query_string=True), name="business"),

    # LP-12: Stock adjustments.
    path(
        "workspace/listings/<int:listing_id>/stock/",
        listing_views.adjust_stock,
        name="adjust-stock",
    ),

    # LP-46: offer and shop photos, fetched from Marketplace so they load on any host.
    path("media/<path:path>", listing_photos.media_proxy, name="media"),

    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
]

# LP-37: the restaurant's three-step request and its status. Kept in its own
# block so other cards can add routes above without touching these lines.
from frontend import apply_views  # noqa: E402
from frontend import review_again_views  # noqa: E402

urlpatterns += [
    path("workspace/apply/", apply_views.restaurant_apply, name="restaurant-apply"),
    path("workspace/apply/<int:step>/", apply_views.restaurant_apply, name="restaurant-apply"),
    path("workspace/request/", apply_views.request_status, name="request-status"),
    # LP-45: a rejected restaurant asks for review again.
    path("workspace/request/review-again/", review_again_views.review_again, name="request-review-again"),
]
