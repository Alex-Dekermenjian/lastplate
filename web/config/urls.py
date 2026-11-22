"""
URL configuration for the web frontend.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.urls import path
from django.views.generic import RedirectView

from frontend import demo_clock_views

from frontend import (
    account_views,
    branch_views,
    browse_views,
    cart,
    console_views,
    customer_console_views,
    customer_views,
    favourite_views,
    health,
    listing_photos,
    listing_views,
    offer_actions,
    notification_views,
    offer_reserve,
    order_views,
    password_views,
    photo_peek,
    pickup_views,
    sales_views,
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
    # LP-54: one tap on a "Blurred with magic" photo (JSON, from photo-peek.js).
    path("offers/<int:listing_id>/peek/", photo_peek.peek_offer, name="offer-peek"),
    # LP-39: a restaurant's page with everything it has left.
    path("restaurants/<int:shop_id>/", browse_views.restaurant_detail, name="restaurant-detail"),
    # LP-82: the heart on a restaurant page, and the customer's Favourites page.
    path("restaurants/<int:shop_id>/favourite/", favourite_views.toggle_favourite, name="favourite-toggle"),
    path("favourites/", favourite_views.favourites, name="favourites"),

    # LP-62: the customer's cart (one restaurant, kept in the session).
    path("cart/", cart.view_cart, name="cart"),
    path("cart/add/<int:listing_id>/", cart.add_to_cart, name="cart-add"),
    path("cart/update/<int:listing_id>/", cart.update_cart, name="cart-update"),
    # LP-19: Reserve, placing the order from the cart.
    path("cart/checkout/", cart.checkout, name="cart-checkout"),

    # LP-78: the customer's order page (shown at the counter) and My orders.
    # LP-41: the bell and the customer's notifications.
    path("notifications/", notification_views.notifications_page, name="notifications"),
    path("notifications/count/", notification_views.notifications_count, name="notifications-count"),
    path("orders/", order_views.my_orders, name="my-orders"),
    path("orders/<uuid:reservation_ref>/", order_views.order_detail, name="order-detail"),
    # LP-27: the customer cancels before the pick-up-by time.
    path("orders/<uuid:reservation_ref>/cancel/", order_views.order_cancel, name="order-cancel"),

    # LP-18: the Search tab of the customer's bottom bar (My orders is LP-78, above).
    path("search/", customer_views.search, name="search"),

    # LP-56: one login page and one sign-up page for every kind of account.
    path("login/", account_views.login, name="login"),
    path("signup/", account_views.signup, name="signup"),
    path("sign-out/", account_views.sign_out, name="sign-out"),
    path("account/", account_views.my_account, name="my-account"),
    # LP-50: forgotten passwords (no email): ask support, get a temporary
    # password from the admin, then choose a new one before anything else.
    path("login/forgot-password/", password_views.forgot_password, name="forgot-password"),
    path("account/new-password/", password_views.choose_new_password, name="choose-new-password"),
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
    # LP-50: find an account and give it a temporary password.
    path("console/accounts/", password_views.console_accounts, name="console-accounts"),
    path("console/accounts/<int:user_id>/temporary-password/", password_views.console_temporary_password,
         name="console-temporary-password"),

    path("workspace/", account_views.workspace, name="workspace"),
    path("workspace/orders/", shop_order_views.today_orders, name="today-orders"),
    path("workspace/sales/", sales_views.history, name="sales-history"),
    path("workspace/sales/export/", sales_views.history, {"export": True}, name="sales-export"),
    path("workspace/orders/count/", shop_order_views.orders_badge, name="orders-badge"),
    path("workspace/orders/pickup/", pickup_views.pickup, name="pickup-code"),
    path("workspace/orders/<uuid:reservation_ref>/paid/", pickup_views.payment, name="pickup-paid"),
    path("workspace/orders/<uuid:reservation_ref>/undo/", pickup_views.payment, {"undo": True}, name="pickup-undo"),
    # LP-79: the restaurant cancels an order, or all of a branch's for today, with a reason.
    path("workspace/orders/<uuid:reservation_ref>/cancel/", shop_order_views.cancel_order, name="shop-cancel-order"),
    path("workspace/orders/cancel-all/", shop_order_views.cancel_all_today, name="shop-cancel-all"),
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
    # LP-54: the owner's real photo, whatever effect customers get.
    path("workspace/listings/<int:listing_id>/photo/", photo_peek.owner_photo, name="owner-offer-photo"),
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
    # LP-29: take a published offer down; reservations stay valid.
    path(
        "workspace/listings/<int:listing_id>/unpublish/",
        offer_actions.unpublish,
        name="unpublish-listing",
    ),
    path(
        "workspace/listings/<int:listing_id>/delete/",
        offer_actions.delete_draft,
        name="delete-draft",
    ),
    # LP-44: repost an ended offer as today's draft.
    path(
        "workspace/listings/<int:listing_id>/repost/",
        offer_actions.repost,
        name="repost-listing",
    ),
    # LP-58: /business is the restaurant's area; it is the workspace.
    path("business/", RedirectView.as_view(pattern_name="workspace", query_string=True), name="business"),

    # LP-12: Stock adjustments.
    path(
        "workspace/listings/<int:listing_id>/stock/",
        listing_views.adjust_stock,
        name="adjust-stock",
    ),

    # LP-46: listing and shop photos, fetched from Marketplace through this site.
    path("media/<path:path>", listing_photos.media_proxy, name="media"),

    path("demo-clock/", demo_clock_views.set_clock, name="demo-clock"),
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